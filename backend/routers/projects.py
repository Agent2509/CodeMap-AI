from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from rag.embeddings import get_chroma_client
from rag.retriever import reset_collection

router = APIRouter()

PROJECTS_DIR = Path(__file__).resolve().parents[1] / "projects"


def _meta(project_id: str) -> dict[str, Any] | None:
    path = PROJECTS_DIR / project_id / "meta.json"
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def project_root(project_id: str | None) -> Path | None:
    """Root directory of an indexed project, or None if unknown."""
    if not project_id:
        return None
    meta = _meta(project_id)
    if not meta or not meta.get("path"):
        return None
    return Path(meta["path"]).resolve()


def _build_tree(files: list[str]) -> list[dict[str, Any]]:
    tree: dict[str, Any] = {}
    for rel in files:
        parts = Path(rel).parts
        cur = tree
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = None

    def conv(name: str, node: Any) -> dict[str, Any]:
        if node is None:
            return {"name": name, "type": "file"}
        return {
            "name": name,
            "type": "dir",
            "children": [conv(k, v) for k, v in sorted(node.items())],
        }

    return [conv(k, v) for k, v in sorted(tree.items())]


@router.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    projects = []
    for col in get_chroma_client().list_collections():
        name = col.name
        meta = _meta(name) or {}
        projects.append(
            {
                "id": name,
                "name": meta.get("name", name),
                "path": meta.get("path", ""),
                "chunks": col.count(),
            }
        )
    return projects


@router.get("/api/projects/{project_id}/tree")
def project_tree(project_id: str) -> dict[str, Any]:
    meta = _meta(project_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="unknown project")
    return {
        "id": project_id,
        "name": meta.get("name", project_id),
        "tree": _build_tree(meta.get("files", [])),
    }


@router.get("/api/projects/{project_id}/file")
def project_file(project_id: str, path: str) -> dict[str, str]:
    meta = _meta(project_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="unknown project")
    root = Path(meta["path"]).resolve()

    # Graph nodes and the agent both hand over absolute paths; accept those when
    # they live inside the project, otherwise treat the value as project-relative.
    candidate = Path(path)
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()

    if not target.is_relative_to(root) or not target.is_file():
        # Citations often carry an extra leading prefix. Fall back to a suffix match
        # so clicking a cited path still opens the right file.
        wanted = path.lstrip("./")
        files: list[str] = meta.get("files", [])
        match = (
            next((f for f in files if f == wanted), None)
            or next((f for f in files if f.endswith("/" + wanted)), None)
            or next((f for f in files if Path(f).name == Path(wanted).name), None)
        )
        if match is None:
            raise HTTPException(status_code=404, detail="file not found")
        target = (root / match).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")

    return {
        "path": str(target.relative_to(root)),
        "content": target.read_text(encoding="utf-8", errors="replace"),
    }


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, str]:
    base = PROJECTS_DIR.resolve()
    target = (PROJECTS_DIR / project_id).resolve()
    if target == base or not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail="invalid project id")
    reset_collection(project_id)
    shutil.rmtree(target, ignore_errors=True)
    return {"status": "deleted"}
