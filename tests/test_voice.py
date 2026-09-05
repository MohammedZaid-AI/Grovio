"""
Speech in, speech out — ai/voice.py and the money gate it guards.

The Sarvam API is stubbed at the SDK boundary and nothing here touches the
network, but the SHAPES are the real ones, read off sarvamai 0.1.32:

    speech_to_text.transcribe -> transcript, language_code, language_probability
    text.translate            -> translated_text
    text_to_speech.convert    -> audios: list[str]   (BASE64, not raw bytes)

That last one is the sort of thing a mock invented from a docstring gets wrong,
so it is pinned here.

The section that matters most is the last one: a transcribed selection must not
reach `place_order` without being read back. Transcription of code-switched
Kannada and English is not reliable enough to spend someone's money on unheard.
"""
import asyncio
import base64
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"
os.environ["WHATSAPP_GATEWAY_SECRET"] = "test-gateway-secret"
os.environ["SARVAM_API_KEY"] = "test-sarvam-key-never-logged"
SPEAKER = "919333300001"
os.environ["AUTHORIZED_PHONES"] = SPEAKER

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from ai import concierge, conversation, identity, memory, skills, voice
from ai.providers import ProviderKind, registry

_passed = _failed = 0


def check(name, condition):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# The real Sarvam response shapes, stubbed
# ----------------------------------------------------------------------
class Transcript:
    def __init__(self, transcript, language_code, probability=0.97):
        self.transcript = transcript
        self.language_code = language_code
        self.language_probability = probability


class Translation:
    def __init__(self, text):
        self.translated_text = text


class Speech:
    """`audios` is a LIST of BASE64 strings — never raw bytes, and possibly
    split across chunks for a long reply."""

    def __init__(self, *chunks):
        self.audios = [base64.b64encode(c).decode() for c in chunks]


class FakeSarvam:
    def __init__(self, transcript=None, translation=None, speech=None):
        self.calls = []
        self._transcript = transcript or Transcript("I want biryani", "en-IN")
        self._translation = translation or Translation("ನನಗೆ ಬಿರಿಯಾನಿ ಬೇಕು")
        self._speech = speech or Speech(b"OggS-fake-audio")

        outer = self

        class STT:
            def transcribe(self, **kw):
                outer.calls.append(("transcribe", kw))
                if isinstance(outer._transcript, Exception):
                    raise outer._transcript
                return outer._transcript

        class Text:
            def translate(self, **kw):
                outer.calls.append(("translate", kw))
                if isinstance(outer._translation, Exception):
                    raise outer._translation
                return outer._translation

        class TTS:
            def convert(self, **kw):
                outer.calls.append(("convert", kw))
                if isinstance(outer._speech, Exception):
                    raise outer._speech
                return outer._speech

        self.speech_to_text = STT()
        self.text = Text()
        self.text_to_speech = TTS()


def with_sarvam(fake):
    return patch.object(voice, "_client", return_value=fake)


print("=" * 70)
print("VOICE — Kannada and English, in and out")
print("=" * 70)

# ======================================================================
print("\n[1] English in")
fake = FakeSarvam(Transcript("I need milk", "en-IN", 0.99))
with with_sarvam(fake):
    text, language, confidence = run(voice.transcribe(b"OggS...", "audio/ogg"))
check("the transcript comes back", text == "I need milk")
check("the language is Sarvam's, not guessed by us", language == "en-IN")
check("confidence is passed through for the money gate", confidence == 0.99)

sent = dict(fake.calls[0][1])
check("language detection is asked for, never assumed",
      sent["language_code"] == "unknown")
check("the pinned speech model is used", sent["model"] == "saaras:v3")
check("translate mode, so the planner stays monolingual",
      sent["mode"] == "translate")
check("the container format is declared", sent["file"][2] == "audio/ogg")

# ======================================================================
print("\n[2] Kannada in — detected, not assumed")
fake = FakeSarvam(Transcript("I want two litres of milk", "kn-IN", 0.94))
with with_sarvam(fake):
    text, language, confidence = run(voice.transcribe(b"OggS...", "audio/ogg"))
check("Kannada speech arrives as ENGLISH text for the planner",
      text == "I want two litres of milk")
check("but the language they SPOKE is remembered", language == "kn-IN")
check("so the reply can go back in Kannada", voice._speakable(language) == "kn-IN")

# ======================================================================
print("\n[3] Code-switched Kannada and English")
# What these users actually say: a Kannada sentence with English nouns in it.
fake = FakeSarvam(Transcript("I need 2 milk packets and bread", "kn-IN", 0.62))
with with_sarvam(fake):
    text, language, confidence = run(voice.transcribe(b"OggS...", "audio/ogg"))
check("the mixed utterance transcribes", "milk" in text and "bread" in text)
check("the dominant language is reported", language == "kn-IN")
check("lower confidence is surfaced, not hidden", confidence == 0.62)
check("and it is below the confident threshold", confidence < voice.CONFIDENT)

# ======================================================================
print("\n[4] Speech out")
fake = FakeSarvam(speech=Speech(b"chunk-one-", b"chunk-two"))
with with_sarvam(fake):
    audio = run(voice.synthesize("Here are three options", "en-IN"))
check("base64 is decoded to real bytes", audio == b"chunk-one-chunk-two")
check("chunks are joined, so a long reply is not truncated",
      audio.startswith(b"chunk-one"))
convert = dict(fake.calls[-1][1])
check("the pinned voice model is used", convert["model"] == "bulbul:v3")
check("opus, which is what a WhatsApp voice note is",
      convert["output_audio_codec"] == "opus")
check("the parameter is language_code, as the SDK declares",
      "language_code" in convert)
check("and the voice sent is one the pinned model has",
      convert["speaker"] in voice.TTS_SPEAKERS)

# ======================================================================
print("\n[5] A Kannada reply is TRANSLATED before it is spoken")
# The transcript arrives in English, so the planner replies in English. Reading
# English aloud in a Kannada voice is not a Kannada reply.
fake = FakeSarvam(translation=Translation("ಮೂರು ಆಯ್ಕೆಗಳಿವೆ"))
with with_sarvam(fake):
    spoken = run(voice.localize("Here are three options", "kn-IN"))
check("the reply is translated for a Kannada speaker", spoken == "ಮೂರು ಆಯ್ಕೆಗಳಿವೆ")
translate = dict(fake.calls[-1][1])
check("from English", translate["source_language_code"] == "en-IN")
check("into the language they spoke", translate["target_language_code"] == "kn-IN")
check("in an everyday register, not textbook formal",
      translate["mode"] == "modern-colloquial")

fake = FakeSarvam()
with with_sarvam(fake):
    same = run(voice.localize("Here are three options", "en-IN"))
check("an English reply is NOT sent through a translator",
      same == "Here are three options" and fake.calls == [])

# A translation failure must not cost the user their reply.
fake = FakeSarvam(translation=RuntimeError("translation down"))
with with_sarvam(fake):
    fallback = run(voice.localize("Here are three options", "kn-IN"))
check("if translation fails, the English text is still spoken",
      fallback == "Here are three options")

# ======================================================================
print("\n[6] Languages the voice model cannot speak")
check("Kannada is speakable", voice._speakable("kn-IN") == "kn-IN")
check("English is speakable", voice._speakable("en-IN") == "en-IN")
check("Konkani is NOT, so it falls back to English rather than guessing",
      voice._speakable("kok-IN") == "en-IN")
check("and so does an unknown code", voice._speakable("zz-ZZ") == "en-IN")

# ======================================================================
print("\n[7] FAILS SOFT — never silence, never a crash")
saved = os.environ.pop("SARVAM_API_KEY")
check("no API key means not configured", not voice.is_configured())
raised = None
try:
    run(voice.transcribe(b"OggS..."))
except voice.VoiceUnavailable as e:
    raised = e
check("and transcription raises VoiceUnavailable, not a bare crash",
      raised is not None)
os.environ["SARVAM_API_KEY"] = saved

for name, fake in (
    ("transcription", FakeSarvam(transcript=RuntimeError("service down"))),
    ("synthesis", FakeSarvam(speech=RuntimeError("service down"))),
):
    failed = None
    with with_sarvam(fake):
        try:
            if name == "transcription":
                run(voice.transcribe(b"OggS..."))
            else:
                run(voice.synthesize("hello", "en-IN"))
        except voice.VoiceUnavailable as e:
            failed = e
    check(f"a {name} outage is VoiceUnavailable, catchable at the boundary",
          failed is not None)

empty = None
with with_sarvam(FakeSarvam(Transcript("", "en-IN"))):
    try:
        run(voice.transcribe(b"OggS..."))
    except voice.VoiceUnavailable as e:
        empty = e
check("a voice note with nothing in it is not an empty message", empty is not None)

# ======================================================================
print("\n[8] Nothing spoken is logged")
import io
import logging
from core.logger import logger as app_logger

buffer = io.StringIO()
handler = logging.StreamHandler(buffer)
app_logger.addHandler(handler)
with with_sarvam(FakeSarvam(Transcript("my address is 18-2-29 Attavar", "kn-IN"))):
    run(voice.transcribe(b"OggS..."))
with with_sarvam(FakeSarvam()):
    run(voice.synthesize("Your order is on its way to Attavar", "en-IN"))
app_logger.removeHandler(handler)
logged = buffer.getvalue()

check("the transcript is never logged", "Attavar" not in logged)
check("the API key is never logged", "test-sarvam-key-never-logged" not in logged)
check("but the length and language are, for debugging",
      "language=kn-IN" in logged and "bytes" in logged)

# ======================================================================
print("\n[9] MONEY: a SPOKEN selection is read back before it is ordered")
# The whole point. A typed "2" is what the person meant; a transcribed "2" is a
# speech model's guess about code-switched speech, and the first anyone would
# know of a mis-hearing is the wrong food at the door.
OFFERS = [
    {"provider": "voice_food", "id": "R-1::MI-1", "title": "Chicken Biryani",
     "venue": "Meghana Foods", "price": 340, "currency": "INR",
     "eta_minutes": 22, "kind": "restaurant"},
    {"provider": "voice_food", "id": "R-2::MI-2", "title": "Andhra Biryani",
     "venue": "Nagarjuna", "price": 410, "currency": "INR",
     "eta_minutes": 38, "kind": "restaurant"},
]


class Spy:
    """An ordering provider that records whether money was ever committed."""
    name = "voice_food"
    display_name = "Voice Food"
    kind = ProviderKind.RESTAURANT
    supports_tracking = False
    supports_cancellation = False
    supports_coupons = False
    supports_history = False

    def __init__(self):
        self.placed = []

    async def search(self, query, ctx):
        return []

    async def place(self, offer, quantity, ctx):
        from ai.providers.base import PLACED, PlacedOrder
        self.placed.append(offer.id)
        return PlacedOrder(provider=self.name, order_id="V-1", status=PLACED,
                           total=340)


spy = Spy()
registry.clear()
registry.register(spy)
identity.load(SPEAKER)


def offer_list():
    conversation.reset(SPEAKER)
    conversation.show_offers(SPEAKER, OFFERS, "biryani")


# --- spoken: must be confirmed first
offer_list()
db.note_voice_turn(SPEAKER, "kn-IN")
from ai.planner import _resolve_state_first

instruction = run(_resolve_state_first(identity.load(SPEAKER), "two"))
state = conversation.load(SPEAKER)

check("a spoken selection does NOT order immediately", spy.placed == [])
check("the conversation waits on a spoken confirmation",
      state.state == conversation.State.AWAITING_SPOKEN_CONFIRMATION)
check("the chosen option is parked, not lost", state.pending.selection == 2)
check("the model is told to read the item back",
      "HEARD ALOUD, NOT YET ORDERED" in instruction)
check("with the real item name", "Andhra Biryani" in instruction)
check("and the real price, so a mis-hearing is audible",
      "410" in instruction)
check("and told plainly that nothing is ordered yet",
      "do NOT claim anything is ordered" in instruction)

# --- "yes" is what releases the money
confirmed = run(_resolve_state_first(identity.load(SPEAKER), "yes"))
check("saying yes places the order", spy.placed == ["R-2::MI-2"])
check("and it is the option they actually said, not the first one",
      spy.placed == ["R-2::MI-2"])

# --- "no" spends nothing
spy.placed.clear()
offer_list()
db.note_voice_turn(SPEAKER, "kn-IN")
run(_resolve_state_first(identity.load(SPEAKER), "one"))
refused = run(_resolve_state_first(identity.load(SPEAKER), "no"))
check("saying no orders nothing", spy.placed == [])
check("and says so without claiming a charge", "Nothing was charged" in refused)

# --- a corrected number is read back again, not ordered
spy.placed.clear()
offer_list()
db.note_voice_turn(SPEAKER, "kn-IN")
run(_resolve_state_first(identity.load(SPEAKER), "one"))
corrected = run(_resolve_state_first(identity.load(SPEAKER), "no, two"))
check("changing their mind mid-confirmation orders nothing", spy.placed == [])

# --- TYPED selections are unaffected
spy.placed.clear()
offer_list()
db.clear_voice_turn(SPEAKER)
run(_resolve_state_first(identity.load(SPEAKER), "2"))
check("a TYPED selection still orders immediately, as it always did",
      spy.placed == ["R-2::MI-2"])

# ======================================================================
print("\n[10] The reply comes back spoken, and the turn is recorded as voice")
registry.clear()
LISTENER = "919333300002"
identity.load(LISTENER)
conversation.reset(LISTENER)
db.note_voice_turn(LISTENER, "kn-IN")

spoken_out = []


async def _capture(phone, audio, mimetype="audio/ogg; codecs=opus"):
    spoken_out.append((phone, audio))
    return "WAMSG-VOICE-OUT"


with patch("ai.concierge.plan", new=AsyncMock(return_value="Here are three options")), \
     patch("whatsapp.send_audio", new=_capture), \
     with_sarvam(FakeSarvam(speech=Speech(b"OggS-reply"))):
    reply = run(concierge.respond(LISTENER, "I want biryani"))

check("the text reply is returned as always, for the normal queue",
      reply == "Here are three options")
check("AND a voice note is sent", len(spoken_out) == 1)
check("to the right person", spoken_out[0][0] == LISTENER)
check("carrying real audio bytes", spoken_out[0][1] == b"OggS-reply")
check("the voice turn is consumed, so the next typed reply is not spoken",
      db.peek_voice_turn(LISTENER) is None)

stored = db.get_connection().execute(
    "SELECT role, source FROM conversation_history WHERE phone = ? ORDER BY id",
    (LISTENER,)).fetchall()
check("the transcript is stored, so context and follow-ups still work",
      any(role == "user" for role, _ in stored))
check("tagged as voice, which is what lets a later turn know they speak",
      ("user", "voice") in [(r, s) for r, s in stored])

# A synthesis failure must still leave the user with their text reply.
spoken_out.clear()
db.note_voice_turn(LISTENER, "kn-IN")
with patch("ai.concierge.plan", new=AsyncMock(return_value="Still useful")), \
     patch("whatsapp.send_audio", new=_capture), \
     with_sarvam(FakeSarvam(speech=RuntimeError("tts down"))):
    degraded = run(concierge.respond(LISTENER, "again"))
check("speech failing does not cost the user their reply", degraded == "Still useful")
check("and no broken audio is sent", spoken_out == [])

# A typed turn is completely unchanged.
conversation.reset(LISTENER)
db.clear_voice_turn(LISTENER)
spoken_out.clear()
with patch("ai.concierge.plan", new=AsyncMock(return_value="Typed answer")), \
     patch("whatsapp.send_audio", new=_capture):
    typed = run(concierge.respond(LISTENER, "I want biryani"))
check("a typed turn gets no voice note", spoken_out == [])
check("and answers exactly as before", typed == "Typed answer")

# ======================================================================
print("\n[11] LAYERING: the planner never learns speech exists")
import tokenize
from pathlib import Path

root = Path(__file__).resolve().parent.parent
offenders = []
for path in (root / "ai" / "planner.py", root / "ai" / "skills.py",
             root / "ai" / "memory.py", root / "ai" / "recommendation.py"):
    with open(path, "rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type == tokenize.NAME and token.string in ("voice", "sarvamai"):
                # planner may ASK whether the turn was spoken; it may not
                # transcribe, synthesize or import the speech module.
                offenders.append(f"{path.name}:{token.start[0]}")
source = (root / "ai" / "planner.py").read_text(encoding="utf-8")
check("the planner does not import the speech module",
      "from ai import voice" not in source and "import voice" not in source)
check("nor name the speech vendor", "sarvam" not in source.lower())
# Prose may MENTION WhatsApp (a voice note is an Opus file because WhatsApp
# says so). What must not exist is an IMPORT, so this parses rather than greps.
import ast

tree = ast.parse((root / "ai" / "voice.py").read_text(encoding="utf-8"))


def imported_names(node, top_level_only):
    for child in (node.body if top_level_only else ast.walk(node)):
        if isinstance(child, ast.Import):
            yield from (alias.name for alias in child.names)
        elif isinstance(child, ast.ImportFrom):
            yield child.module or ""


everywhere = set(imported_names(tree, top_level_only=False))
module_level = set(imported_names(tree, top_level_only=True))

check(f"ai/voice.py imports no transport, anywhere ({sorted(everywhere)})",
      not any("whatsapp" in name or "gateway" in name for name in everywhere))
check("the speech vendor is imported lazily, so a missing key cannot break "
      "startup",
      not any("sarvam" in name.lower() for name in module_level)
      and any("sarvam" in name.lower() for name in everywhere))

print("\n" + "=" * 70)
# ======================================================================
print("\n[12] REGRESSION: a model name we pin must still exist")
# Live failure 2026-09-05:
#   "Model 'bulbul:v2' has been deprecated. Please use 'bulbul:v3' instead."
# Nothing in the code said which models were current, so the first sign was a
# real user getting no voice reply. The SDK types every model it accepts, and
# that is checkable without spending an API call.
import typing
import inspect as _inspect
from sarvamai import SarvamAI as _SDK

_probe = _SDK(api_subscription_key="unused-no-call-is-made")


def accepted(method, parameter):
    """The Literal values the SDK's own type hints accept for one argument."""
    annotation = _inspect.signature(method).parameters[parameter].annotation
    for arm in typing.get_args(annotation):
        values = typing.get_args(arm)
        if values and all(isinstance(v, str) for v in values):
            return list(values)
    return []


tts_models = accepted(_probe.text_to_speech.convert, "model")
stt_models = accepted(_probe.speech_to_text.transcribe, "model")
tts_speakers = accepted(_probe.text_to_speech.convert, "speaker")
tts_languages = accepted(_probe.text_to_speech.convert, "language_code")
codecs = accepted(_probe.text_to_speech.convert, "output_audio_codec")

check(f"the SDK types its speech models ({tts_models} / {stt_models})",
      bool(tts_models) and bool(stt_models))
check(f"our voice model is one the SDK accepts ({voice.TTS_MODEL})",
      voice.TTS_MODEL in tts_models)
check(f"our speech model is one the SDK accepts ({voice.STT_MODEL})",
      voice.STT_MODEL in stt_models)

# THE CHECK THAT WOULD HAVE CAUGHT IT. bulbul:v2 was still typed by the SDK
# while the API refused it, but v3 sat beside it — so "are we on the newest the
# SDK offers?" fails the moment a successor appears, which is the same moment
# deprecation starts. Speech is exempt: saaras:v4 exists and staying on v3 is a
# deliberate choice, recorded here so the exemption is visible rather than
# silent.
def newest(models):
    return sorted(models)[-1]


check(f"the voice model is the newest the SDK offers ({newest(tts_models)})",
      voice.TTS_MODEL == newest(tts_models))
check("the speech model is pinned deliberately, not by newest — "
      f"{newest(stt_models)} exists and translate mode is proven on "
      f"{voice.STT_MODEL}",
      voice.STT_MODEL in stt_models)

# The bug this change would ALSO have shipped: voices do not survive a model
# bump. anushka was a bulbul:v2 voice and is not in the v3 roster, so moving
# the model alone would have swapped one API error for another.
check("every voice we might use is one the SDK accepts",
      voice.TTS_SPEAKERS <= set(tts_speakers))
check(f"our default voice is valid ({voice.FALLBACK_SPEAKER})",
      voice.FALLBACK_SPEAKER in tts_speakers)
check("a retired bulbul:v2 voice is NOT silently sent to v3",
      "anushka" not in voice.TTS_SPEAKERS)

_saved_speaker = os.environ.get("SARVAM_SPEAKER")
os.environ["SARVAM_SPEAKER"] = "anushka"
check("and configuring one falls back instead of failing the call",
      voice._speaker() == voice.FALLBACK_SPEAKER)
os.environ["SARVAM_SPEAKER"] = "kavitha"
check("while a real v3 voice is honoured", voice._speaker() == "kavitha")
if _saved_speaker is None:
    os.environ.pop("SARVAM_SPEAKER", None)
else:
    os.environ["SARVAM_SPEAKER"] = _saved_speaker

# The rest of the call surface, so a model bump cannot quietly invalidate it.
check(f"our codec survives the model change ({voice.TTS_CODEC})",
      voice.TTS_CODEC in codecs)
check("every language we speak is one the model speaks",
      voice.TTS_LANGUAGES <= set(tts_languages))
check("Kannada and English specifically",
      "kn-IN" in tts_languages and "en-IN" in tts_languages)

# v3 kept the response shape: a list of base64 strings, possibly chunked.
from sarvamai.types.text_to_speech_response import TextToSpeechResponse

# --- The THIRD live failure, and the last of this shape:
#   "OPUS codec requires one of these sample rates: 8000, 12000, 16000, 24000,
#    48000 Hz. Current sample rate: 22050 Hz."
# We never set a rate, so that 22050 was the SERVER's default — and Sarvam
# documents the default as 24000. Relying on any default is the bug: opus
# accepts a different set from the API at large, and 22050 is in one and not
# the other.
check(f"a sample rate is sent EXPLICITLY, never left to the server "
      f"({voice.TTS_SAMPLE_RATE} Hz)",
      isinstance(voice.TTS_SAMPLE_RATE, int))
check("and it is one the chosen codec accepts",
      voice.TTS_SAMPLE_RATE in voice.OPUS_SAMPLE_RATES)
check("the rate that failed live is correctly excluded",
      22050 not in voice.OPUS_SAMPLE_RATES)
check("the codec we ask for is the one the rate was checked against",
      voice.TTS_CODEC == "opus")

# The general guard, so a fourth surprise cannot arrive the same way: every
# keyword we actually send must be one this SDK's convert() declares.
_convert_params = set(_inspect.signature(_probe.text_to_speech.convert).parameters)
_sent = set()


class _Recorder:
    audios = []

    def convert(self, **kw):
        _sent.update(kw)
        raise RuntimeError("stop here — the keywords are what matters")


class _Probe:
    text_to_speech = _Recorder()


with patch.object(voice, "_client", return_value=_Probe()):
    try:
        run(voice.synthesize("hello", "en-IN"))
    except voice.VoiceUnavailable:
        pass

check(f"every keyword we send is declared by the SDK ({sorted(_sent)})",
      _sent <= _convert_params)
check("we send NO parameter that bulbul:v3 dropped from v2 "
      "(pitch, loudness, enable_preprocessing)",
      not (_sent & voice.V2_ONLY_PARAMS))
check("pace is not sent, so its narrowed v3 range cannot bite",
      "pace" not in _sent)
check("temperature is not sent either — a v3-only knob we do not need",
      "temperature" not in _sent)
check("but the four that matter ARE sent",
      {"text", "language_code", "model", "speaker"} <= _sent)
check("including the sample rate", "speech_sample_rate" in _sent)

check("the response is still `audios`, so the decode/join logic stands",
      "audios" in TextToSpeechResponse.model_fields)
check("and still a LIST of strings, not raw bytes",
      TextToSpeechResponse.model_fields["audios"].annotation
      in (typing.List[str], list[str]))

print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 70)
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
