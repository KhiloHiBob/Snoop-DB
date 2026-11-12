"""Configuration loading.

Private values (hosts, ports, resource names) live in ``config/local.yaml`` which is git-ignored.
The committed ``config/config.example.yaml`` contains placeholders only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_LIMITS: dict[str, Any] = {
    "statement_timeout_ms": 15000,
    "max_rows": 500,
    "star_rows": 50,
    "max_result_bytes": 2_000_000,
    "max_cell_chars": 2000,
    "fan_out_max_databases": 25,
}

DEFAULT_LLM: dict[str, Any] = {
    "backend": "claude_cli",   # your local Claude login (default) | anthropic_api | auto
    "model": "claude-sonnet-5-5",
    "max_steps": 8,
    "max_tokens": 2048,
    "allow_prod_rows_to_llm": False,
}

DEFAULT_EXCLUDED_DATABASES = ["postgres", "rdsadmin", "template0", "template1"]

STORES = ("postgres", "singlestore", "redis", "mongo", "opensearch")


def resolve_secret(section: dict[str, Any], key: str) -> str | None:
    """Return ``section[key_env]`` from the environment, or the literal ``section[key]``."""
    env_name = section.get(f"{key}_env")
    if env_name:
        return os.environ.get(env_name)
    value = section.get(key)
    return None if value in (None, "") else str(value)


@dataclass
class EnvConfig:
    name: str
    production: bool = False
    company_id_enforced: bool = False
    keywords: list[str] = field(default_factory=list)
    description: str = ""
    sdm_resource: str | None = None
    stores: dict[str, dict[str, Any]] = field(default_factory=dict)

    def has_store(self, store: str) -> bool:
        return store in self.stores


@dataclass
class Config:
    llm: dict[str, Any]
    limits: dict[str, Any]
    environments: dict[str, EnvConfig]
    exclude_databases: list[str]
    company_id_exempt_tables: list[str]
    source: str = ""

    @property
    def allow_prod_rows_to_llm(self) -> bool:
        return bool(self.llm.get("allow_prod_rows_to_llm", False))


def _build_env(name: str, raw: dict[str, Any]) -> EnvConfig:
    stores = {s: dict(raw[s]) for s in STORES if isinstance(raw.get(s), dict)}
    production = bool(raw.get("production", False))
    return EnvConfig(
        name=name,
        production=production,
        # Production-like environments enforce company_id predicates unless told otherwise.
        company_id_enforced=bool(raw.get("company_id_enforced", production)),
        keywords=[str(k).lower() for k in raw.get("keywords", [])],
        description=str(raw.get("description", "")),
        sdm_resource=raw.get("sdm_resource"),
        stores=stores,
    )


def config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit)
    if os.environ.get("DB_DOG_CONFIG"):
        return Path(os.environ["DB_DOG_CONFIG"])
    local = ROOT / "config" / "local.yaml"
    return local if local.exists() else ROOT / "config" / "config.example.yaml"


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    p = config_path(path)
    raw = yaml.safe_load(p.read_text()) or {}
    envs = {name: _build_env(name, body or {}) for name, body in (raw.get("environments") or {}).items()}
    if not envs:
        raise ValueError(f"{p}: no environments configured")
    return Config(
        llm={**DEFAULT_LLM, **(raw.get("llm") or {})},
        limits={**DEFAULT_LIMITS, **(raw.get("limits") or {})},
        environments=envs,
        exclude_databases=list(raw.get("exclude_databases") or DEFAULT_EXCLUDED_DATABASES),
        company_id_exempt_tables=[str(t).lower() for t in raw.get("company_id_exempt_tables") or []],
        source=str(p),
    )
