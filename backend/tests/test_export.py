from __future__ import annotations

import json
from pathlib import Path

import pytest

import routers.export as export


def test_markdown_includes_mermaid_and_hubs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "PROJECTS_DIR", tmp_path)
    project_id = "p1"
    (tmp_path / project_id).mkdir()
    (tmp_path / project_id / "meta.json").write_text(json.dumps({"name": "demo"}))

    graph = {
        "nodes": [
            {"id": "a.py", "type": "file", "name": "a.py"},
            {"id": "b.py", "type": "file", "name": "b.py"},
            {"id": "a.py:function:f", "type": "function", "name": "f"},
        ],
        "links": [
            {"source": "a.py", "target": "b.py"},
            {"source": "a.py", "target": "a.py:function:f"},
        ],
    }

    md = export._markdown(project_id, graph)
    assert "# demo — Architecture" in md
    assert "```mermaid" in md
    assert "F0 --> F1" in md
    assert "## Hub Files" in md
    assert "- `a.py` — 2 connections" in md


def test_routes_registered() -> None:
    from main import app

    paths = set(app.openapi()["paths"].keys())
    expected = {
        "/api/upload",
        "/api/sample",
        "/api/upload/status/{task_id}",
        "/api/projects",
        "/api/projects/{project_id}/tree",
        "/api/projects/{project_id}/file",
        "/api/export/markdown/{project_id}",
        "/api/chat/stream",
        "/api/overview",
        "/api/graph",
    }
    assert expected <= paths
