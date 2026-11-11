"""FastAPI app: chat API, direct read-only query API and the static chat UI.

Bound to localhost only (see ``__main__``). Host-header and CORS checks stop a random web page
from talking to a locally running instance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import __version__
from .agent import Agent, AnthropicLLM
from .agent.llm import LLM, LLMUnavailable
from .config import Config, load_config
from .connectors import ConnectorHub
from .connectors.postgres import PolicyError
from .envs import resolve_environment
from .preflight import PreflightError

WEB_DIR = Path(__file__).resolve().parent / "web"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    environment: str = "auto"
    llm_sees_rows: bool = False
    history: list[ChatMessage] = []


class QueryRequest(BaseModel):
    environment: str = "local"
    store: str = "postgres"            # postgres | singlestore | redis
    database: str | None = None
    statement: str = Field(min_length=1, max_length=20000)


def create_app(config: Config | None = None, llm: LLM | None = None, hub: ConnectorHub | None = None) -> FastAPI:
    config = config or load_config()
    hub = hub or ConnectorHub(config)
    llm = llm or AnthropicLLM(config.llm["model"], int(config.llm["max_tokens"]))
    agent = Agent(config, hub, llm)

    app = FastAPI(title="Snoop-DB", version=__version__)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|null)$",
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "config": Path(config.source).name}

    @app.get("/api/environments")
    def environments() -> list[dict[str, Any]]:
        # Never expose hosts, ports or resource names to the browser.
        return [
            {"name": e.name, "production": e.production, "description": e.description, "stores": list(e.stores)}
            for e in config.environments.values()
        ]

    @app.get("/api/environments/{name}/health")
    def env_health(name: str) -> dict[str, Any]:
        if name not in config.environments:
            raise HTTPException(404, f"unknown environment '{name}'")
        try:
            return {"environment": name, **hub.get(name, "postgres").health()}
        except (PreflightError, KeyError) as e:
            raise HTTPException(503, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}") from e

    @app.post("/api/chat")
    def chat(req: ChatRequest) -> dict[str, Any]:
        try:
            return agent.run(
                req.message, req.environment, req.llm_sees_rows, [m.model_dump() for m in req.history]
            )
        except LLMUnavailable as e:
            raise HTTPException(503, str(e)) from e
        except KeyError as e:
            raise HTTPException(400, str(e.args[0])) from e

    @app.post("/api/query")
    def query(req: QueryRequest) -> dict[str, Any]:
        """Run one read-only statement directly (no LLM). Same guard and limits as the agent."""
        try:
            res = resolve_environment(config, "", req.environment)
            conn = hub.get(res.env, req.store)
            if req.store == "redis":
                out = conn.run(req.statement)
            elif req.store in ("postgres", "singlestore"):
                out = conn.query(req.database, req.statement)
            else:
                raise HTTPException(400, f"store '{req.store}' is not supported on this endpoint")
        except PolicyError as e:
            raise HTTPException(422, f"rejected: {e}") from e
        except PreflightError as e:
            raise HTTPException(503, str(e)) from e
        except KeyError as e:
            raise HTTPException(400, str(e.args[0])) from e
        env = config.environments[res.env]
        return {"environment": env.name, "production": env.production, "store": req.store,
                "database": req.database, **out.to_dict()}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    return app
