from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter()

PROJECTS_DIR = Path(__file__).resolve().parents[1] / "projects"


def _graph(project_id: str) -> dict[str, Any]:
    base = PROJECTS_DIR.resolve()
    target = (PROJECTS_DIR / project_id / "graph.json").resolve()
    if not target.is_relative_to(base) or not target.exists():
        raise HTTPException(status_code=404, detail="unknown project")
    data: dict[str, Any] = json.loads(target.read_text())
    return data


def _markdown(project_id: str, data: dict[str, Any]) -> str:
    meta_path = PROJECTS_DIR / project_id / "meta.json"
    name = project_id
    if meta_path.exists():
        name = json.loads(meta_path.read_text()).get("name", project_id)

    nodes = data.get("nodes", [])
    links = data.get("links", [])
    files = {n["id"]: n.get("name", n["id"]) for n in nodes if n.get("type") == "file"}

    degree: dict[str, int] = {}
    for link in links:
        for end in (link.get("source"), link.get("target")):
            node_id = end.get("id") if isinstance(end, dict) else end
            if isinstance(node_id, str):
                degree[node_id] = degree.get(node_id, 0) + 1

    index = {fid: f"F{i}" for i, fid in enumerate(files)}
    edges = []
    for link in links:
        source = link.get("source")
        target = link.get("target")
        source = source.get("id") if isinstance(source, dict) else source
        target = target.get("id") if isinstance(target, dict) else target
        if source in index and target in index:
            edges.append(f"    {index[source]} --> {index[target]}")

    counts: dict[str, int] = {}
    for node in nodes:
        t = node.get("type", "unknown")
        counts[t] = counts.get(t, 0) + 1

    hubs = sorted(files, key=lambda f: degree.get(f, 0), reverse=True)[:5]

    lines = [
        f"# {name} — Architecture",
        "",
        "## Dependency Diagram (file level)",
        "",
        "```mermaid",
        "graph TD",
        *[f'    {index[fid]}["{fname}"]' for fid, fname in files.items()],
        *edges,
        "```",
        "",
        "## Hub Files",
        "",
        *[f"- `{fid}` — {degree.get(fid, 0)} connections" for fid in hubs],
        "",
        "## Entity Counts",
        "",
        *[f"- {t}: {c}" for t, c in sorted(counts.items())],
        "",
    ]
    return "\n".join(lines)


@router.get("/api/export/markdown/{project_id}")
def export_markdown(project_id: str) -> Response:
    data = _graph(project_id)
    body = _markdown(project_id, data)
    return Response(
        content=body,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{project_id}-ARCHITECTURE.md"'},
    )
