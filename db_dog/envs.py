"""Environment resolution: pick an environment from the wording of a question.

Default is ``local``. If several environments are mentioned the riskiest-named one wins
(it is read-only anyway) and the ambiguity is reported so the answer can say so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Config

# Built-in keywords, used when the config does not give any for an environment name.
BUILTIN_KEYWORDS: dict[str, list[str]] = {
    "payroll-prod": ["payroll prod", "payroll production", "payroll-prod", "payroll-production"],
    "prod": ["prod", "production"],
    "sandbox": ["sandbox"],
    "staging": ["staging", "stage"],
    "local": ["local", "localhost"],
}

# Tie-break order when more than one environment is mentioned (most specific first).
PRIORITY = ["payroll-prod", "prod", "sandbox", "staging", "local"]

DEFAULT_ENV = "local"


@dataclass
class Resolution:
    env: str
    reason: str
    ambiguous: list[str] = field(default_factory=list)


def _keywords(config: Config, env: str) -> list[str]:
    configured = config.environments[env].keywords
    return configured or BUILTIN_KEYWORDS.get(env, [env])


def resolve_environment(config: Config, question: str, explicit: str | None = None) -> Resolution:
    if explicit and explicit.lower() != "auto":
        if explicit not in config.environments:
            raise KeyError(f"Unknown environment '{explicit}'. Known: {', '.join(config.environments)}")
        return Resolution(explicit, "selected explicitly")

    text = question.lower()
    matched: dict[str, str] = {}
    for env in config.environments:
        for kw in sorted(_keywords(config, env), key=len, reverse=True):
            if re.search(rf"(?<![\w-]){re.escape(kw)}(?![\w-])", text):
                matched[env] = kw
                break

    if not matched:
        default = DEFAULT_ENV if DEFAULT_ENV in config.environments else next(iter(config.environments))
        return Resolution(default, "no environment named in the question; using the default")

    # PRIORITY puts the more specific environment first ("payroll prod" before "prod").
    ordered = sorted(matched, key=lambda e: PRIORITY.index(e) if e in PRIORITY else len(PRIORITY))
    chosen = ordered[0]
    # A generic keyword nested inside the chosen one ("prod" inside "payroll prod") is not ambiguity.
    ambiguous = [e for e in ordered[1:] if matched[e] not in matched[chosen]]
    return Resolution(chosen, f"matched '{matched[chosen]}' in the question", ambiguous)
