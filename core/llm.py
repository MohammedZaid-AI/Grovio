"""
LLM client — async, multi-turn, tool-calling.

The previous version took a single `system` + `user` string pair and blocked the
event loop. A concierge needs real conversations (history, follow-ups) and needs
the model to *orchestrate* components rather than answer in one shot, so this
exposes a message list plus tool definitions.

Groq is reached through its OpenAI-compatible endpoint, so there is exactly one
client and one code path regardless of provider.
"""
import json
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from core.config import Config
from core.logger import logger

GROQ_OPENAI_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _parse_arguments(raw: str) -> dict:
    """Tool arguments arrive as a JSON string and models sometimes emit junk.
    A malformed call must not crash the turn — the planner surfaces it back to
    the model as a failed tool result instead."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(f"LLM emitted non-JSON tool arguments: {raw!r}")
        return {}
    return parsed if isinstance(parsed, dict) else {}


class LLM:
    def __init__(self):
        if Config.OPENAI_API_KEY:
            api_key, base_url = Config.OPENAI_API_KEY, Config.OPENAI_BASE_URL
        elif Config.GROQ_API_KEY:
            api_key, base_url = Config.GROQ_API_KEY, GROQ_OPENAI_BASE_URL
        else:
            raise RuntimeError("No LLM credentials: set OPENAI_API_KEY or GROQ_API_KEY.")

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.base_url = base_url

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> LLMReply:
        """One completion. Returns the assistant's text and/or its tool calls."""
        request: dict[str, Any] = {
            "model": Config.MODEL,
            "temperature": Config.TEMPERATURE if temperature is None else temperature,
            "max_tokens": 2048,
            "messages": messages,
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"

        response = await self._client.chat.completions.create(**request)
        message = response.choices[0].message

        calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_arguments(call.function.arguments),
            )
            for call in (message.tool_calls or [])
        ]
        return LLMReply(text=(message.content or "").strip(), tool_calls=calls)


_llm: LLM | None = None


def get_llm() -> LLM:
    """Lazily built so importing this module never requires credentials."""
    global _llm
    if _llm is None:
        _llm = LLM()
    return _llm
