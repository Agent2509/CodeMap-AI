from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from chromadb.api.types import Metadata
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from parser.chunker import AstSemanticChunker
from parser.graph_builder import CodebaseGraphBuilder
from parser.tree_sitter_parser import CodeEntity, MultiLanguageParser
from rag.retriever import get_retriever, reset_collection

router = APIRouter()

PROJECTS_DIR = Path(__file__).resolve().parents[1] / "projects"
CLONES_DIR = Path(__file__).resolve().parents[1] / "clones"
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_project"
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "chroma_data",
    ".next",
    "build",
    "dist",
    ".opencode",
    "projects",
    "clones",
    # Tool output and caches: generated, never part of the codebase under analysis.
    "graphify-out",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
# Credential files are never indexed. They would otherwise be embedded into the
# vector store and replayed into the model's context by search_codebase.
SECRET_FILE_RE = re.compile(
    r"(^\.env(\.|$)"
    r"|^\.(netrc|pgpass|htpasswd|npmrc|pypirc|git-credentials)$"
    r"|^id_(rsa|dsa|ecdsa|ed25519)$"
    r"|\.(pem|key|p12|pfx|jks|keystore)$)",
    re.IGNORECASE,
)
HEADER_RE = re.compile(r"# File: (.*?) \| Type: (.*?) \| Name: (.*?) \| Lines: (\d+)-(\d+)")

# Only these URL shapes are handed to git; anything else is rejected before a
# subprocess is ever spawned.
GIT_URL_RE = re.compile(r"^(https?://[\w.\-]+/[\w.\-/]+|git@[\w.\-]+:[\w.\-/]+?)(\.git)?/?$")

TASKS: dict[str, dict[str, Any]] = {}


class UploadRequest(BaseModel):
    path: str


class CloneRequest(BaseModel):
    url: str


def _project_id(root: Path) -> str:
    return hashlib.sha1(str(root).encode()).hexdigest()[:12]


def _repo_name(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return re.sub(r"\.git$", "", tail)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _iter_files(root: Path) -> list[Path]:
    # Skip names are matched against the path *inside* root, not the absolute path:
    # a repo that merely lives under .../projects/myapp (or .../build/x) must not be
    # skipped wholesale. It also lets a clone destination under clones/ be indexed.
    return [
        f
        for f in root.rglob("*")
        if f.is_file()
        and not SKIP_DIRS & set(f.relative_to(root).parts[:-1])
        and not SECRET_FILE_RE.search(f.name)
    ]


def _parse_and_chunk(
    file: Path, parser: MultiLanguageParser, chunker: AstSemanticChunker
) -> tuple[list[CodeEntity], list[str]]:
    """One file to (graph entities, vector chunks).

    Shared by _index and the retrieval eval so the harness measures the same
    pipeline the indexer runs, rather than a reimplementation of it.
    """
    entities = parser.parse_file(str(file))
    return entities, chunker.chunk_file(file, entities)


def _index(task_id: str, root: Path, project_id: str, name: str | None = None) -> None:
    task = TASKS[task_id]
    try:
        task.update(status="running", progress=5)

        files = _iter_files(root)
        parser = MultiLanguageParser()
        chunker = AstSemanticChunker()
        entities: list[CodeEntity] = []
        chunks: list[str] = []
        total = max(1, len(files))
        for i, file in enumerate(files, 1):
            file_entities, file_chunks = _parse_and_chunk(file, parser, chunker)
            entities.extend(file_entities)
            chunks.extend(file_chunks)
            task["progress"] = 5 + int(i / total * 65)

        graph = CodebaseGraphBuilder()
        graph.build_graph(entities)
        data = graph.export_d3_json()

        out = PROJECTS_DIR / project_id
        out.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data)
        # Written per project only. A global current_graph.json was overwritten by
        # every index, so the "no project selected" view showed whichever repo was
        # indexed last.
        (out / "graph.json").write_text(payload)
        (out / "insights.json").write_text(json.dumps(graph.insights()))
        (out / "meta.json").write_text(
            json.dumps(
                {
                    "id": project_id,
                    "path": str(root),
                    "name": name or root.name,
                    "files": [str(f.relative_to(root)) for f in files],
                }
            )
        )

        task["progress"] = 75
        metadatas: list[Metadata] = []
        ids: list[str] = []
        for i, chunk in enumerate(chunks):
            m = HEADER_RE.match(chunk)
            if m:
                file_path, e_type, name, start, end = m.groups()
                meta: Metadata = {
                    "file_path": file_path,
                    "type": e_type,
                    "name": name,
                    "start_line": int(start),
                    "end_line": int(end),
                }
            else:
                file_path, e_type, name = "", "", ""
                meta = {"file_path": "", "type": "", "name": "", "start_line": 0, "end_line": 0}
            metadatas.append(meta)
            ids.append(f"{i}:{file_path}:{e_type}:{name}")

        reset_collection(project_id)
        retriever = get_retriever(project_id)
        batch_size = 5000
        for i in range(0, len(chunks), batch_size):
            retriever.add_chunks(
                chunks[i : i + batch_size],
                metadatas[i : i + batch_size],
                ids[i : i + batch_size],
            )
            if chunks:
                task["progress"] = 75 + int(min(1, (i + batch_size) / len(chunks)) * 25)

        task.update(status="done", progress=100, entities_found=len(entities))
    except Exception as exc:  # surface to SSE; never leave the client polling forever
        task.update(status="error", error=str(exc))


def _start_index(root: Path, background: BackgroundTasks) -> dict[str, str]:
    project_id = _project_id(root)
    task_id = uuid.uuid4().hex
    TASKS[task_id] = {"status": "queued", "progress": 0, "project_id": project_id}
    background.add_task(_index, task_id, root, project_id)
    return {"task_id": task_id, "project_id": project_id}


@router.post("/api/upload")
def upload(req: UploadRequest, background: BackgroundTasks) -> dict[str, str]:
    root = Path(req.path).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {req.path}")
    return _start_index(root, background)


@router.post("/api/sample")
def sample(background: BackgroundTasks) -> dict[str, str]:
    if not SAMPLE_DIR.is_dir():
        raise HTTPException(status_code=404, detail="sample project missing")
    return _start_index(SAMPLE_DIR, background)


def _clone_and_index(task_id: str, url: str, project_id: str) -> None:
    task = TASKS[task_id]
    dest = CLONES_DIR / project_id
    try:
        task.update(status="running", progress=2)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # List form (no shell) — the URL is an argv entry, never interpreted.
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", url, str(dest)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git clone failed")
    except (subprocess.SubprocessError, OSError, RuntimeError) as exc:
        task.update(status="error", error=f"clone failed: {exc}")
        return
    _index(task_id, dest, project_id, name=_repo_name(url))


@router.post("/api/clone")
def clone(req: CloneRequest, background: BackgroundTasks) -> dict[str, str]:
    url = req.url.strip()
    if not GIT_URL_RE.match(url):
        raise HTTPException(status_code=400, detail="provide a valid git repository URL")
    if shutil.which("git") is None:
        raise HTTPException(status_code=500, detail="git is not installed on the server")
    project_id = hashlib.sha1(url.encode()).hexdigest()[:12]
    task_id = uuid.uuid4().hex
    TASKS[task_id] = {"status": "queued", "progress": 0, "project_id": project_id}
    background.add_task(_clone_and_index, task_id, url, project_id)
    return {"task_id": task_id, "project_id": project_id}


def _status_stream(task_id: str) -> Iterator[str]:
    while True:
        task = TASKS.get(task_id)
        if task is None:
            yield _sse({"status": "error", "error": "unknown task"})
            return
        # Snapshot: the indexing thread mutates this dict, and serialising a dict
        # that changes size underneath json.dumps raises mid-stream — which killed
        # the SSE response before the final "done" event.
        yield _sse(dict(task))
        if task["status"] in ("done", "error"):
            # The client closes the stream on this event, so the record is done with.
            # Without this TASKS grows for the life of the process.
            TASKS.pop(task_id, None)
            return
        time.sleep(0.25)


@router.get("/api/upload/status/{task_id}")
def upload_status(task_id: str) -> StreamingResponse:
    return StreamingResponse(_status_stream(task_id), media_type="text/event-stream")
