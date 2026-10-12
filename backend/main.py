from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx


def _load_env(path: Path) -> None:
    # .env is the single source of truth for LLM config: base URL, key and model
    # always move together. Letting a stale shell AI_BASE_URL win here would pair one
    # provider's URL with another's model name and 400 every request.
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip("\"'")


_load_env(Path(__file__).resolve().parent / ".env")

from routers import chat, export, graph, overview, projects, upload  # noqa: E402
from agent.react_agent import MODEL

app = FastAPI(title="CodeMap AI")

# Only the local frontend may call the API. A wildcard here, combined with the
# no-auth /api/upload (which accepts any absolute path), let any page you visit
# read your filesystem through the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    """Quick config + LLM reachability check. Use before the demo to confirm the model is loaded."""
    base = os.getenv("AI_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    key = os.getenv("AI_API_KEY") or "missing"
    llm_ok = False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
            llm_ok = r.status_code == 200
    except Exception:
        llm_ok = False
    return {"status": "ok", "model": MODEL, "llm_reachable": llm_ok}


app.include_router(upload.router)
app.include_router(graph.router)
app.include_router(chat.router)
app.include_router(overview.router)
app.include_router(projects.router)
app.include_router(export.router)
