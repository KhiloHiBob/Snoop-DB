from pathlib import Path

import httpx
import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from snoop_db.agent import Agent
from snoop_db.config import Settings
from snoop_db.validation import UnsafeQuery


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[str] = Field(default_factory=list, max_length=6)


def create_app(settings=None, agent=None):
    settings = settings or Settings.from_env()
    agent = agent or Agent(settings)
    app = FastAPI(title="Snoop-DB", docs_url=None, redoc_url=None)
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(UnsafeQuery)
    async def unsafe_query(request, exc):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_operation(request, exc):
        return JSONResponse(status_code=400, content={
            "error": "Operation refused. Check the SELECT-only database role, allowlist, and provider output."
        })

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={
            "error": "Database request failed or timed out. Check configuration and query validity."
        })

    @app.exception_handler(httpx.HTTPError)
    async def provider_error(request, exc):
        return JSONResponse(status_code=502, content={"error": "AI provider request failed."})

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse(status_code=500, content={"error": "Request failed safely."})

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "provider": settings.provider,
                "send_results_to_llm": settings.send_results_to_llm}

    @app.get("/api/schema")
    def schema():
        return agent.schema()

    @app.post("/api/chat")
    def chat(body: ChatRequest):
        if any(len(question) > 4000 for question in body.history):
            return JSONResponse(status_code=422, content={"error": "History question is too long."})
        return agent.chat(body.question, body.history)

    return app
