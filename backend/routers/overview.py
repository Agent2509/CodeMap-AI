from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

from agent.react_agent import MODEL, client

router = APIRouter()

GRAPH_PATH = Path(__file__).resolve().parents[1] / "current_graph.json"
MAX_CHARS_PER_FILE = 2000
SNIPPET_FILES = 5


class OverviewRequest(BaseModel):
    project_id: str | None = None

PROJECTS_DIR = Path(__file__).resolve().parents[1] / "projects"

def _top_files(project_id: str | None = None, limit: int = SNIPPET_FILES) -> list[str]:
    target_path = GRAPH_PATH
    if project_id:
        target_path = PROJECTS_DIR / project_id / "graph.json"
        
    if not target_path.exists():
        return []
    graph: dict[str, Any] = json.loads(target_path.read_text())
    degree: dict[str, int] = {}
    for link in graph.get("links", []):
        for end in (link.get("source"), link.get("target")):
            node_id = end.get("id") if isinstance(end, dict) else end
            if isinstance(node_id, str):
                degree[node_id] = degree.get(node_id, 0) + 1
    files = [n["id"] for n in graph.get("nodes", []) if n.get("type") == "file"]
    files.sort(key=lambda f: degree.get(f, 0), reverse=True)
    return files[:limit]


@router.post("/api/overview")
async def overview(req: OverviewRequest) -> dict[str, str]:
    files = _top_files(req.project_id)
    if not files:
        return {"summary": "No codebase indexed yet. Analyze a path first."}

    sections = []
    for path in files:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sections.append(f"# {path}\n{text[:MAX_CHARS_PER_FILE]}")

    messages = cast(
        list[ChatCompletionMessageParam],
        [
            {
                "role": "user",
                "content": (
                    "Provide a 3-sentence high-level architectural summary of this codebase.\n\n"
                    + "\n\n".join(sections)
                ),
            }
        ],
    )
    try:
        completion = await client.chat.completions.create(model=MODEL, messages=messages)
    except Exception as exc:  # boundary: surface a readable message, not a bare 500
        return {"summary": f"Overview unavailable. Check the LLM settings (model `{MODEL}`): {exc}"}
    return {"summary": completion.choices[0].message.content or ""}
