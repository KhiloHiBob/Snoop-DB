"""Per-module DB skills (knowledge_local/modules/*.md, cropped from the Bob monolith by tools/crop_modules.py).

The system prompt always lists the available modules; the full text of a module is preloaded when the
question (or recent conversation) mentions it, and the agent can pull any other one with ``module_skill``.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import ROOT

MODULES_DIR = ROOT / "knowledge_local" / "modules"
MAX_MODULE_CHARS = 60_000
MAX_PRELOAD = 2

# extra words that mean a module (the module name itself always counts)
ALIASES: dict[str, str] = {
    "timeoff": "time off leave pto vacation sick absence holiday loa_policy balance accrual",
    "timeattendance": "attendance timesheet punch clock time-entry worktime",
    "calendar": "holiday calendars public holidays",
    "employee": "employees people profile hire termination lifecycle",
    "employer": "company employer entity subsidiary",
    "position": "positions headcount job position workforce",
    "site": "site sites working pattern",
    "analytics": "kpi kpis headcount turnover attrition report",
    "workflows": "workflow workflows task",
    "compensation": "salary pay band bands",
    "payroll": "payroll payslip pay run",
}


def available() -> list[str]:
    return sorted(p.stem for p in MODULES_DIR.glob("*.md")) if MODULES_DIR.is_dir() else []


def _words(slug: str) -> list[str]:
    return [slug, *ALIASES.get(slug, "").split()]


def select(text: str, limit: int = MAX_PRELOAD) -> list[str]:
    low = text.lower()
    scored = []
    for slug in available():
        hits = sum(1 for w in _words(slug) if re.search(rf"(?<![a-z]){re.escape(w.lower())}", low))
        if hits:
            scored.append((hits, slug))
    ranked = sorted(scored, key=lambda x: (-x[0], x[1]))
    return [s for i, (h, s) in enumerate(ranked[:limit]) if i == 0 or h >= 2]   # a weak second hit is not worth the prompt size


def load(slug: str) -> str:
    path = MODULES_DIR / f"{slug}.md"
    if slug not in available() or not path.is_file():
        raise KeyError(f"unknown module '{slug}'. Available: {', '.join(available()) or 'none'}")
    return path.read_text()[:MAX_MODULE_CHARS]


def index_text() -> str:
    mods = available()
    if not mods:
        return ""
    return ("## Bob modules with a DB skill\n" + ", ".join(mods) +
            "\nWhen a question concerns one of these, its skill is preloaded; otherwise call `module_skill` "
            "with the module name BEFORE guessing tables. Table names are often plural "
            "(timeoff_requests), and the monolith database is `hibob`.")


def preload_text(question: str) -> str:
    return "\n\n".join(load(s) for s in select(question))
