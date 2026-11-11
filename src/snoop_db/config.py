import os
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    allowed_relations: tuple[str, ...]
    dialect: str = "postgres"
    provider: str = "demo"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"
    llm_api_key: str = field(default="", repr=False)
    send_results_to_llm: bool = False
    max_rows: int = 100
    statement_timeout_ms: int = 5000

    def __post_init__(self):
        if not self.database_url or not self.allowed_relations:
            raise ValueError("DATABASE_URL and ALLOWED_RELATIONS are required")
        if any(not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", name)
               or name.split(".")[0] in {"pg_catalog", "information_schema"}
               or name.split(".")[0].startswith("pg_")
               for name in self.allowed_relations):
            raise ValueError("Allowlist must contain explicit schema.table names")
        if not 1 <= self.max_rows <= 1000 or not 1 <= self.statement_timeout_ms <= 60000:
            raise ValueError("Result limit or timeout is out of range")
        if self.provider not in {"demo", "openai"}:
            raise ValueError("Unknown LLM provider")
        if self.provider == "openai" and not self.llm_api_key:
            raise ValueError("LLM_API_KEY is required for the OpenAI-compatible provider")
        if self.provider == "openai" and not self.llm_base_url.startswith("https://"):
            raise ValueError("LLM_BASE_URL must use HTTPS")

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            allowed_relations=tuple(x.strip() for x in
                                    os.environ.get("ALLOWED_RELATIONS", "").split(",") if x.strip()),
            dialect=os.environ.get("DB_DIALECT", "postgres"),
            provider=os.environ.get("LLM_PROVIDER", "demo"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_model=os.environ.get("LLM_MODEL", "gpt-4.1-mini"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            send_results_to_llm=os.environ.get("SEND_RESULTS_TO_LLM", "false").lower() == "true",
            max_rows=int(os.environ.get("MAX_ROWS", "100")),
            statement_timeout_ms=int(os.environ.get("STATEMENT_TIMEOUT_MS", "5000")),
        )
