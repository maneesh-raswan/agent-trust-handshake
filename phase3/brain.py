"""
The brain: one call that turns (system prompt, conversation, tools) into the model's
next move. An agent's loop is the same whichever brain is plugged in:

  AnthropicBrain   a real Claude model (needs credentials: see config.py)
  ScriptedBrain    a deterministic stand-in for CI and free runs (see stub_brain.py)

Messages use the Anthropic Messages format throughout: an assistant turn is a list of
content blocks, tool results go back in a user turn of `tool_result` blocks.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import config


@dataclass
class Turn:
    text: str = ""                                   # what the model said
    tool_calls: list[dict] = field(default_factory=list)  # [{"id", "name", "input"}]
    stop: str = "end"                                # "tool_use" | "end" | "refusal" | "max_tokens"
    assistant_content: Any = None                    # exactly what to append to the history


class Brain(Protocol):
    async def decide(self, system: str, messages: list, tools: list[dict]) -> Turn: ...


class AnthropicBrain:
    def __init__(self, role: str, fallbacks: bool = True, max_tokens: int = 16000):
        import anthropic  # imported here so phases 1 and 2 never need the package

        config.require_credentials()
        self._client = anthropic.AsyncAnthropic()
        self._model = config.model(role)
        self._effort = os.environ.get("PHASE3_EFFORT")
        self._max_tokens = max_tokens
        # Server-side refusal fallback is on by default for the agents doing ordinary work, on the
        # models that support it. The red-team agent turns it off: if the model declines to attack,
        # that is an acceptable outcome, not something to route around.
        self._fallbacks = (fallbacks and self._model in config.FALLBACK_MODELS
                           and os.environ.get("PHASE3_FALLBACKS", "1") != "0")

    async def decide(self, system: str, messages: list, tools: list[dict]) -> Turn:
        params: dict[str, Any] = dict(model=self._model, max_tokens=self._max_tokens, system=system,
                                      messages=messages)
        if tools:
            params["tools"] = tools
        if self._effort:
            params["output_config"] = {"effort": self._effort}
        if self._fallbacks:
            response = await self._client.beta.messages.create(
                **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        else:
            response = await self._client.messages.create(**params)
        return self._to_turn(response)

    @staticmethod
    def _to_turn(response) -> Turn:
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "input": b.input or {}}
                 for b in response.content if b.type == "tool_use"]
        stop = {"tool_use": "tool_use", "refusal": "refusal", "max_tokens": "max_tokens"}.get(
            response.stop_reason, "end")
        # response.content goes back unchanged: it carries the model's thinking blocks, which
        # must be replayed as returned.
        return Turn(text=text, tool_calls=calls, stop=stop, assistant_content=response.content)
