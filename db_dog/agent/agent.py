"""The agent loop: resolve environment -> tool-use loop -> structured response."""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..connectors import ConnectorHub
from ..envs import resolve_environment
from .llm import LLM
from .prompts import build_system_prompt
from .tools import ToolRuntime, tool_schemas


class Agent:
    def __init__(self, config: Config, hub: ConnectorHub, llm: LLM):
        self.config = config
        self.hub = hub
        self.llm = llm

    def rows_visible(self, env_name: str, requested: bool) -> tuple[bool, str | None]:
        """Rows go to the LLM only when asked for, and never for production unless explicitly enabled."""
        if not requested:
            return False, None
        env = self.config.environments[env_name]
        if env.production and not self.config.allow_prod_rows_to_llm:
            return False, (
                f"Sending rows to the LLM is disabled for '{env_name}' "
                "(set llm.allow_prod_rows_to_llm in your local config to change this)."
            )
        return True, None

    def run(
        self,
        message: str,
        environment: str | None = "auto",
        llm_sees_rows: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        resolution = resolve_environment(self.config, message, environment)
        env = self.config.environments[resolution.env]
        visible, note = self.rows_visible(env.name, llm_sees_rows)
        stores = self.hub.stores_for(env.name)

        rt = ToolRuntime(self.config, self.hub, env, visible)
        if note:
            rt.warnings.append(note)
        if resolution.ambiguous:
            rt.warnings.append(
                f"Several environments were mentioned; using '{env.name}' "
                f"(also mentioned: {', '.join(resolution.ambiguous)}). Pick one in the selector to be explicit."
            )

        system = build_system_prompt(self.config, env, visible, stores)
        tools = tool_schemas(stores)
        messages: list[dict[str, Any]] = [
            {"role": m["role"], "content": m["content"]}
            for m in (history or [])[-10:] if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        messages.append({"role": "user", "content": message})

        summary = ""
        max_steps = int(self.config.llm["max_steps"])
        for _ in range(max_steps):
            resp = self.llm.create(system, messages, tools)
            text = "".join(b["text"] for b in resp.content if b["type"] == "text").strip()
            calls = [b for b in resp.content if b["type"] == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                summary = text
                break
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": c["id"], "content": rt.call(c["name"], c["input"])}
                    for c in calls
                ],
            })
        else:
            summary = "I stopped after the maximum number of steps. Here is what I found so far."
            rt.warnings.append(f"step limit ({max_steps}) reached")

        return {
            "environment": env.name,
            "production": env.production,
            "environment_reason": resolution.reason,
            "summary": summary,
            "results": rt.results,
            "steps": rt.steps,
            "warnings": rt.warnings,
            "rows_sent_to_llm": visible,
        }
