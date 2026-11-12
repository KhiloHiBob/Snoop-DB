"""System prompt assembly from the knowledge files."""

from __future__ import annotations

from pathlib import Path

from ..config import ROOT, Config, EnvConfig
from . import modules

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
LOCAL_KNOWLEDGE_DIR = ROOT / "knowledge_local"   # git-ignored, private notes

STORE_DOCS = {"singlestore": "singlestore.md", "redis": "redis.md", "mongo": "mongo.md", "opensearch": "opensearch.md"}
ALWAYS = ["postgres-rules.md", "big-tables.md", "multi-database.md", "schema-conventions.md", "investigation.md"]

BASE = """You are db-dog, a careful, read-only database agent. You answer questions about data by
discovering schemas and running read-only queries with the provided tools, then summarising.

Hard rules:
- You can only read. Writes, DDL and admin commands are rejected by the tools, do not attempt them.
- Discover before you query: do not guess table or column names.
- Big or company-scoped tables: if the user gave no company id, ASK for it before querying (see big-tables).
- Be economical: use the fewest tool calls that answer the question.
- The user sees every result table directly. Do NOT copy table contents into your answer.
- If a tool returns an error, read it, fix the query once or twice, then explain plainly what blocked you.
- If rows are withheld from you (privacy), say what the query returns and refer to the table; never invent values.
- Final answer: 1-4 short sentences, markdown allowed (**bold**, `code`). Always state the environment
  and database that were queried.
"""


def _read(path: Path) -> str:
    return path.read_text().strip()


def knowledge_text(env: EnvConfig) -> str:
    parts = [_read(KNOWLEDGE_DIR / n) for n in ALWAYS if (KNOWLEDGE_DIR / n).exists()]
    for store, fname in STORE_DOCS.items():
        if env.has_store(store) and (KNOWLEDGE_DIR / fname).exists():
            parts.append(_read(KNOWLEDGE_DIR / fname))
    if LOCAL_KNOWLEDGE_DIR.is_dir():
        parts += [_read(p) for p in sorted(LOCAL_KNOWLEDGE_DIR.glob("*.md"))]
    return "\n\n".join(parts)


def build_system_prompt(config: Config, env: EnvConfig, rows_visible_to_llm: bool, stores: list[str], question: str = "") -> str:
    kind = "PRODUCTION (view-only, company_id predicate required)" if env.production else "non-production"
    visibility = (
        "Result rows ARE visible to you." if rows_visible_to_llm
        else "Result rows are NOT visible to you (privacy): you only see column names and row counts."
    )
    ctx = (
        f"Environment: **{env.name}** - {kind}. {env.description}\n"
        f"Configured stores: {', '.join(stores) or 'none'}. {visibility}\n"
        f"Default row limit {config.limits['max_rows']} (50 for SELECT *)."
    )
    mods = modules.index_text()
    pre = modules.preload_text(question) if question else ""
    return (f"{BASE}\n## Context\n{ctx}\n\n## Knowledge\n{knowledge_text(env)}"
            + (f"\n\n{mods}" if mods else "") + (f"\n\n## Preloaded module skills\n{pre}" if pre else ""))
