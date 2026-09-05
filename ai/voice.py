"""
Speech in, speech out. A boundary concern, nothing more.

    voice note  ->  transcribe  ->  TEXT  ->  planner  ->  TEXT
                                                            |
                             synthesize  <-  localize  <----+
                                  |
                             voice note

The planner, skills, memory and the provider layer never learn this module
exists. They receive text and return text exactly as they do for a typed
message — voice is handled entirely at the edges, in `backend/routes.py` on the
way in and `ai/concierge.py` on the way out.

This module imports nothing from `whatsapp/` or the gateway, matching the rule
that `ai/`, `backend/` and `core/` never name the transport. It takes bytes and
returns bytes; who carried them is not its business.

WHY THREE FUNCTIONS AND NOT TWO
Transcription runs in `translate` mode, so a Kannada speaker's words arrive as
English and the planner stays monolingual. But the reply then comes back in
English too, and reading English aloud in a Kannada voice is not a Kannada
reply — it is English with an accent. `localize` puts the reply back into the
language the person actually spoke, before it is voiced.
"""
import asyncio
import base64
import os

from core.logger import logger

# Sarvam's own vocabulary. Only these two matter for this product, but the
# detected code is passed through as-is so a third language would simply work.
ENGLISH = "en-IN"
KANNADA = "kn-IN"

# What the speech models are asked for. Pinned rather than left to a default so
# an SDK upgrade cannot silently change what a user hears.
STT_MODEL = "saaras:v3"
TTS_MODEL = "bulbul:v3"
TRANSLATE_MODEL = "mayura:v1"

# `translate` hands the planner English whatever was spoken, so search queries
# and dish names stay in the one language the rest of the system reasons in.
STT_MODE = "translate"
# Everyday register, not textbook formal — these are people ordering dinner.
TRANSLATE_MODE = "modern-colloquial"

# WhatsApp voice notes are Opus in an Ogg container; asking for anything else
# means transcoding somewhere.
TTS_CODEC = "opus"

# NEVER LEFT TO THE SERVER DEFAULT. Sarvam documents the default as 24000 Hz,
# but a live bulbul:v3 call reported "Current sample rate: 22050 Hz" and
# refused it — opus accepts a DIFFERENT set from the API at large:
#
#   API generally : 8000, 16000, 22050, 24000, 32000, 44100, 48000
#   OPUS only     : 8000, 12000, 16000, 24000, 48000
#
# 22050 sits in the first list and not the second, so the default is a trap for
# anyone asking for opus. 24000 is in both, is Sarvam's own documented default,
# and is wideband speech quality — right for a voice note.
OPUS_SAMPLE_RATES = frozenset({8000, 12000, 16000, 24000, 48000})
TTS_SAMPLE_RATE = 24000

# Parameters bulbul:v2 accepted that v3 does NOT. Recorded so a future tweak
# cannot reintroduce one: pitch, loudness and enable_preprocessing were dropped
# in v3, and pace narrowed from 0.3-3.0 to 0.5-2.0. We send none of them.
V2_ONLY_PARAMS = frozenset({"pitch", "loudness", "enable_preprocessing"})

# VOICES ARE NOT INTERCHANGEABLE BETWEEN MODELS. The bulbul:v2 roster was seven
# names (anushka, manisha, vidya, arya, abhilash, karun, hitesh); v3 has its own
# and shares none of them. Moving the model without moving the speaker trades
# "model deprecated" for "unknown speaker" — the same call failing for a new
# reason. An unrecognised override falls back rather than failing a reply.
TTS_SPEAKERS = frozenset({
    "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan",
    "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
    "manan", "sumit", "roopa", "kabir", "aayan", "ashutosh", "advait", "anand",
    "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
    "mohit", "kavitha", "rehan", "soham", "rupali",
})
FALLBACK_SPEAKER = "shubh"          # bulbul:v3's own default


def _speaker() -> str:
    """The configured voice, or the model's default if it is not one this model
    can actually speak with."""
    chosen = (os.getenv("SARVAM_SPEAKER") or "").strip().lower()
    if chosen and chosen not in TTS_SPEAKERS:
        logger.warning(
            f"[voice] SARVAM_SPEAKER={chosen!r} is not a {TTS_MODEL} voice — "
            f"using {FALLBACK_SPEAKER}. (The bulbul:v2 voices were retired with "
            f"the model.)"
        )
        return FALLBACK_SPEAKER
    return chosen or FALLBACK_SPEAKER

# Text-to-speech supports fewer languages than transcription. Anything outside
# this set is voiced in English rather than guessed at.
TTS_LANGUAGES = frozenset({
    "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN",
    "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN",
})

# Below this, the transcript is a guess. Not used to reject anything — it is
# handed to the caller so a MONEY decision can demand confirmation.
CONFIDENT = 0.7


class VoiceUnavailable(RuntimeError):
    """Speech could not be handled: no API key, or the service failed.

    Always caught at the boundary and turned into a plain text reply. A voice
    note must never produce silence — a silent bot reads as a broken one.
    """


def is_configured() -> bool:
    return bool(os.getenv("SARVAM_API_KEY"))


def _client():
    """Built per call, not at import. FAILS CLOSED: with no key this raises
    rather than letting a voice note vanish into a stack trace."""
    if not is_configured():
        raise VoiceUnavailable("SARVAM_API_KEY is not set")
    try:
        from sarvamai import SarvamAI
    except ImportError as e:                       # pragma: no cover
        raise VoiceUnavailable("the sarvamai package is not installed") from e
    return SarvamAI(api_subscription_key=os.getenv("SARVAM_API_KEY"))


def _speakable(language_code: str) -> str:
    """A language the voice model can actually speak, or English."""
    return language_code if language_code in TTS_LANGUAGES else ENGLISH


async def transcribe(audio_bytes: bytes, mimetype: str = "audio/ogg") -> tuple:
    """One voice note -> (text, detected_language_code, confidence).

    Language detection is Sarvam's (`language_code="unknown"`), not ours: the
    speaker may switch between Kannada and English mid-sentence, and guessing
    from the transcript afterwards would be inventing a fact.

    The text comes back in ENGLISH regardless of what was spoken, so everything
    above this line stays monolingual. `detected` is the language they actually
    used, and is what the reply gets spoken in.
    """
    if not audio_bytes:
        raise VoiceUnavailable("empty audio")

    client = _client()

    def _call():
        return client.speech_to_text.transcribe(
            # A filename is what tells the API the container format.
            file=("voice.ogg", audio_bytes, mimetype),
            model=STT_MODEL,
            mode=STT_MODE,
            language_code="unknown",       # detect; never assume
        )

    try:
        # The SDK is synchronous. Off the event loop, or one voice note stalls
        # every other phone's worker.
        response = await asyncio.to_thread(_call)
    except Exception as e:
        logger.error(f"[voice] transcription failed: {type(e).__name__}: {e}")
        raise VoiceUnavailable("transcription failed") from e

    text = (getattr(response, "transcript", "") or "").strip()
    detected = getattr(response, "language_code", None) or ENGLISH
    confidence = getattr(response, "language_probability", None)

    # Length and language only. The transcript is a person's own words and does
    # not belong in a log line.
    logger.info(f"[voice] transcribed {len(audio_bytes)} bytes -> "
                f"{len(text)} chars, language={detected} "
                f"confidence={confidence if confidence is None else round(confidence, 2)}")
    if not text:
        raise VoiceUnavailable("nothing was said")
    return text, detected, confidence


async def localize(text: str, language_code: str) -> str:
    """Put a reply back into the language it will be spoken in.

    A no-op for English. Best effort otherwise: if translation fails the caller
    still gets usable English rather than nothing, because a reply in the wrong
    language beats silence.
    """
    target = _speakable(language_code)
    if not text or target == ENGLISH:
        return text

    client = _client()

    def _call():
        return client.text.translate(
            input=text,
            source_language_code=ENGLISH,
            target_language_code=target,
            model=TRANSLATE_MODEL,
            mode=TRANSLATE_MODE,
        )

    try:
        response = await asyncio.to_thread(_call)
    except Exception as e:
        logger.warning(f"[voice] could not translate the reply to {target}: "
                       f"{type(e).__name__}. Speaking it in English instead.")
        return text

    translated = (getattr(response, "translated_text", "") or "").strip()
    return translated or text


async def synthesize(text: str, language_code: str = ENGLISH) -> bytes:
    """Reply text -> voice note bytes.

    The API returns base64 strings, not raw audio, and may split a long reply
    across several chunks — both are decoded and joined here so callers only
    ever handle bytes.
    """
    if not text:
        raise VoiceUnavailable("nothing to say")

    language = _speakable(language_code)
    client = _client()

    def _call():
        return client.text_to_speech.convert(
            text=text,
            language_code=language,
            model=TTS_MODEL,
            speaker=_speaker(),
            output_audio_codec=TTS_CODEC,
            speech_sample_rate=TTS_SAMPLE_RATE,
        )

    try:
        response = await asyncio.to_thread(_call)
    except Exception as e:
        logger.error(f"[voice] synthesis failed: {type(e).__name__}: {e}")
        raise VoiceUnavailable("synthesis failed") from e

    chunks = getattr(response, "audios", None) or []
    try:
        audio = b"".join(base64.b64decode(chunk) for chunk in chunks)
    except Exception as e:
        raise VoiceUnavailable("audio could not be decoded") from e

    if not audio:
        raise VoiceUnavailable("no audio was returned")
    logger.info(f"[voice] synthesized {len(text)} chars -> {len(audio)} bytes "
                f"in {language}")
    return audio


async def say(text: str, language_code: str) -> bytes:
    """Translate if needed, then voice it. The one call a reply path wants."""
    return await synthesize(await localize(text, language_code), language_code)
