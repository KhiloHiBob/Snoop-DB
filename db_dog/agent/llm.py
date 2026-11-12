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


CLI_PROTOCOL = """
## Tool protocol (you have no native tools in this session)
Reply with ONLY one JSON object, no prose, no code fences:
- to call tools: {"tool_calls": [{"name": "<tool>", "input": {...}}]}
- when you are done: {"final": "<your answer>"}
Available tools (JSON schemas):
"""


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply (tolerates code fences and stray prose)."""
    import json
    import re

    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                esc = (c == "\\") and not esc
                if c == '"' and not esc:
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def render_transcript(messages: list[dict[str, Any]]) -> str:
    import json

    out: list[str] = []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append(f"[{m['role'].upper()}]\n{c}")
            continue
        for b in c:
            if b["type"] == "text":
                out.append(f"[ASSISTANT]\n{b['text']}")
            elif b["type"] == "tool_use":
                out.append("[ASSISTANT TOOL CALL]\n" + json.dumps({"name": b["name"], "input": b["input"]}))
            elif b["type"] == "tool_result":
                out.append("[TOOL RESULT]\n" + str(b["content"]))
    return "\n\n".join(out)


class ClaudeCLILLM:
    """Uses the user's own local Claude Code login (``claude -p``) with every native tool disabled.

    Tool use is emulated with a JSON protocol, so Claude can only *ask* db-dog to run a tool;
    the call still goes through the same guards as with the API backend.
    """

    def __init__(self, model: str | None = None, timeout_s: int = 180, binary: str = "claude"):
        self.model = model
        self.timeout_s = timeout_s
        self.binary = binary
        self._n = 0

    @staticmethod
    def available(binary: str = "claude") -> bool:
        import shutil

        return shutil.which(binary) is not None

    def create(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        import json
        import subprocess
        import tempfile

        if not self.available(self.binary):
            raise LLMUnavailable("The `claude` CLI is not installed or not on PATH.")
        sys_prompt = system + CLI_PROTOCOL + json.dumps(tools)
        cmd = [self.binary, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
               "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "project",
               "--system-prompt", sys_prompt]
        if self.model:
            cmd += ["--model", self.model]
        prompt = render_transcript(messages) + "\n\nReply now with the JSON object only."
        try:
            with tempfile.TemporaryDirectory() as cwd:  # empty dir: no project settings, hooks or CLAUDE.md
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                      timeout=self.timeout_s, cwd=cwd)
        except subprocess.TimeoutExpired as e:
            raise LLMUnavailable(f"`claude` timed out after {self.timeout_s}s") from e
        if proc.returncode != 0:
            raise LLMUnavailable(f"`claude` failed: {(proc.stderr or proc.stdout).strip()[:300]}. "
                                 "Are you logged in? Run `claude` once interactively.")
        try:
            result = str(json.loads(proc.stdout).get("result", ""))
        except ValueError:
            result = proc.stdout
        obj = extract_json(result)
        if obj and isinstance(obj.get("tool_calls"), list) and obj["tool_calls"]:
            blocks = []
            for call in obj["tool_calls"]:
                self._n += 1
                blocks.append({"type": "tool_use", "id": f"cli_{self._n}", "name": str(call.get("name", "")),
                               "input": call.get("input") if isinstance(call.get("input"), dict) else {}})
            return LLMResponse("tool_use", blocks)
        text = str(obj["final"]) if obj and "final" in obj else result.strip()
        return LLMResponse("end_turn", [{"type": "text", "text": text}])


def make_llm(llm_cfg: dict[str, Any]) -> LLM:
    """backend: claude_cli (default, your local Claude login) | anthropic_api | auto (API key if set, else CLI)."""
    backend = str(llm_cfg.get("backend", "claude_cli"))
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if backend == "claude_cli" or (backend == "auto" and not api_key and ClaudeCLILLM.available()):
        return ClaudeCLILLM(llm_cfg.get("cli_model"))
    return AnthropicLLM(llm_cfg["model"], int(llm_cfg.get("max_tokens", 2048)), api_key)


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
