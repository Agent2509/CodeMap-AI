from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

GRAPH_PATH = Path(__file__).resolve().parents[1] / "current_graph.json"
EMPTY_GRAPH: dict[str, list] = {"nodes": [], "links": []}


PROJECTS_DIR = Path(__file__).resolve().parents[1] / "projects"

@router.get("/api/graph")
def get_graph(project_id: str | None = None) -> dict:
    target_path = GRAPH_PATH
    if project_id:
        target_path = PROJECTS_DIR / project_id / "graph.json"
        
    if not target_path.exists():
        return EMPTY_GRAPH
    return json.loads(target_path.read_text())


EMPTY_INSIGHTS: dict = {"nodes": 0, "links": 0, "entity_counts": {}, "cycles": [], "hubs": []}


@router.get("/api/graph/insights")
def get_insights(project_id: str | None = None) -> dict:
    if not project_id:
        return EMPTY_INSIGHTS
    target = PROJECTS_DIR / project_id / "insights.json"
    if not target.exists():
        return EMPTY_INSIGHTS
    return json.loads(target.read_text())
