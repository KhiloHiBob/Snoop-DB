"""Thin LLM wrapper so the agent loop can be tested with a stub."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LLMResponse:
    stop_reason: str                  # "tool_use" | "end_turn" | ...
    content: list[dict[str, Any]]     # [{"type": "text", "text": ...}, {"type": "tool_use", "id", "name", "input"}]


class LLM(Protocol):
    def create(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse: ...


class LLMUnavailable(RuntimeError):
    """No API key / SDK problem: reported to the UI as a configuration error."""


class AnthropicLLM:
    def __init__(self, model: str, max_tokens: int = 2048, api_key: str | None = None):
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    def _get_client(self):
        if not self._api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set. Export it (see .env.example) and restart.")
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def create(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        resp = self._get_client().messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system, messages=messages, tools=tools,
        )
        blocks: list[dict[str, Any]] = []
        for b in resp.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)})
        return LLMResponse(stop_reason=resp.stop_reason or "end_turn", content=blocks)
