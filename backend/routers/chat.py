from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.react_agent import run_agent_stream
from routers.projects import project_root

router = APIRouter()

MAX_HISTORY = 6
# Prior turns are resent with every request, so cap both their number and size —
# long earlier answers would otherwise eat the provider's token budget.
MAX_HISTORY_CHARS = 1000


def _trim_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trimmed: list[dict[str, Any]] = []
    for message in history[-MAX_HISTORY:]:
        content = str(message.get("content") or "")
        if len(content) > MAX_HISTORY_CHARS:
            content = content[:MAX_HISTORY_CHARS] + "\n... [truncated]"
        trimmed.append({**message, "content": content})
    return trimmed


class ChatRequest(BaseModel):
    prompt: str
    project_id: str | None = None
    history: list[dict[str, Any]] | None = None


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _safe_stream(
    prompt: str,
    history: list[dict[str, Any]],
    root: Path | None,
    project_id: str | None,
) -> AsyncGenerator[str, None]:
    # A missing/misconfigured model raises before the first token; turn that into
    # an SSE error event so the UI never sits on a blank, silent stream.
    try:
        async for chunk in run_agent_stream(
            prompt, history, root=root, project_id=project_id
        ):
            yield chunk
    except Exception as exc:
        yield _sse({"type": "error", "text": f"Agent error: {exc}"})
        yield _sse({"type": "done"})


@router.post("/api/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    history = _trim_history(req.history or [])
    # Resolve the project root here (sync, threadpool) but apply it inside the
    # stream — see the note on run_agent_stream.
    return StreamingResponse(
        _safe_stream(req.prompt, history, project_root(req.project_id), req.project_id),
        media_type="text/event-stream",
    )
