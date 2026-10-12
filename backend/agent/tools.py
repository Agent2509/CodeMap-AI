from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from rag.retriever import get_retriever

# Fallback root (the repo) for when no project is active. File paths from the
# agent are relative to the *indexed project's* root, not this one.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECTS_DIR = Path(__file__).resolve().parents[1] / "projects"

# Per-request state, not per-process. A plain global here let two chats about
# different projects overwrite each other mid-request.
_active_root: ContextVar[Path | None] = ContextVar("_active_root", default=None)
_active_project_id: ContextVar[str | None] = ContextVar("_active_project_id", default=None)


def set_project_root(root: str | Path | None) -> None:
    _active_root.set(Path(root).resolve() if root else None)


def set_active_project(project_id: str | None, root: str | Path | None) -> None:
    _active_project_id.set(project_id)
    set_project_root(root)


def get_active_root() -> Path | None:
    return _active_root.get()


def _graph() -> dict[str, Any]:
    """Load the active project's graph (read fresh — it is small and re-indexed often)."""
    project_id = _active_project_id.get()
    if not project_id:
        return {"nodes": [], "links": []}
    path = PROJECTS_DIR / project_id / "graph.json"
    if not path.exists():
        return {"nodes": [], "links": []}
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _end(value: Any) -> str:
    return value.get("id") if isinstance(value, dict) else str(value)


def _matches(node: dict[str, Any], symbol: str) -> bool:
    name = str(node.get("name", ""))
    if not name:
        return False
    needle = symbol.strip().lower()
    if name.lower() == needle:
        return True
    file_path = str(node.get("file_path") or node.get("id") or "")
    if Path(file_path).name.lower() == needle:
        return True
    return file_path.lower().endswith("/" + needle)


# Tool output is replayed into every later request, so it is capped hard: an
# unbounded file dump or five long chunks will blow a provider's per-request /
# tokens-per-minute limit on its own.
MAX_SEARCH_CHUNKS = 5
MAX_CHUNK_CHARS = 900
MAX_FILE_LINES = 200


def search_codebase(query: str) -> list[str]:
    chunks = get_retriever(_active_project_id.get()).search(query, top_k=MAX_SEARCH_CHUNKS)
    trimmed: list[str] = []
    for chunk in chunks:
        if len(chunk) > MAX_CHUNK_CHARS:
            chunk = f"{chunk[:MAX_CHUNK_CHARS]}\n... [truncated, {len(chunk)} chars total]"
        trimmed.append(chunk)
    return trimmed


def get_file_content(
    filepath: str, start_line: int | None = None, end_line: int | None = None
) -> str:
    root = _active_root.get() or PROJECT_ROOT
    target = (root / filepath).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"path traversal blocked: {filepath}")

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    if total == 0:
        return f"{filepath} is empty."

    start = max(1, start_line or 1)
    if start > total:
        return f"{filepath} has only {total} lines."
    end = min(total, end_line or start + MAX_FILE_LINES - 1)
    if end < start:
        end = start

    body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
    if end < total:
        body += (
            f"\n... ({total - end} more lines; call get_file_content with "
            "start_line/end_line to read a range)"
        )
    return body


def find_definition(symbol: str) -> str:
    graph = _graph()
    hits = [
        n
        for n in graph.get("nodes", [])
        if _matches(n, symbol) and n.get("type") in ("function", "class", "file")
    ]
    if not hits:
        return f"No definition found for '{symbol}'."
    lines = []
    for n in hits[:10]:
        start, end = n.get("start_line"), n.get("end_line")
        loc = f"{n.get('file_path')}:{start}-{end}" if start else str(n.get("id"))
        lines.append(f"- {n.get('type')} `{n.get('name')}` at {loc}")
    return "\n".join(lines)


def find_references(symbol: str) -> str:
    graph = _graph()
    nodes = graph.get("nodes", [])
    targets = {n["id"]: n for n in nodes if _matches(n, symbol)}
    if not targets:
        return f"No symbol '{symbol}' in the graph."

    by_id = {n["id"]: n for n in nodes}
    refs: list[str] = []
    seen: set[str] = set()
    for link in graph.get("links", []):
        source, target = _end(link.get("source")), _end(link.get("target"))
        if target in targets and source not in targets and source not in seen:
            seen.add(source)
            node = by_id.get(source, {})
            refs.append(f"- {node.get('name', source)} ({source})")
    if not refs:
        return f"No references to '{symbol}' found."
    return f"{len(refs)} reference(s) to '{symbol}':\n" + "\n".join(refs[:15])


def _resolve_node(graph: dict[str, Any], token: str) -> str | None:
    for node in graph.get("nodes", []):
        if node["id"] == token or _matches(node, token):
            return str(node["id"])
    return None


def get_dependency_path(source: str, target: str) -> str:
    graph = _graph()
    start, goal = _resolve_node(graph, source), _resolve_node(graph, target)
    if start is None or goal is None:
        missing = source if start is None else target
        return f"Could not find '{missing}' in the dependency graph."

    adjacency: dict[str, list[str]] = {}
    for link in graph.get("links", []):
        adjacency.setdefault(_end(link.get("source")), []).append(_end(link.get("target")))

    prev: dict[str, str] = {}
    queue = deque([start])
    seen = {start}
    while queue:
        current = queue.popleft()
        if current == goal:
            break
        for nxt in adjacency.get(current, []):
            if nxt not in seen:
                seen.add(nxt)
                prev[nxt] = current
                queue.append(nxt)

    if goal not in seen:
        return f"No dependency path from '{source}' to '{target}'."
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return " -> ".join(path)


def _impact_ids(node: str, limit: int = 60) -> tuple[str | None, list[tuple[str, int]]]:
    """Reverse-dependency closure over dependency edges (imports/calls).

    Containment edges (file -> its own entity) are skipped, or every file would
    appear to be affected by its own contents.
    """
    graph = _graph()
    target = _resolve_node(graph, node)
    if target is None:
        return None, []

    reverse: dict[str, list[str]] = {}
    for link in graph.get("links", []):
        if link.get("kind") not in ("import", "call"):
            continue
        source, sink = _end(link.get("source")), _end(link.get("target"))
        reverse.setdefault(sink, []).append(source)

    seen: dict[str, int] = {target: 0}
    queue = deque([target])
    while queue:
        current = queue.popleft()
        for source in reverse.get(current, []):
            if source not in seen:
                seen[source] = seen[current] + 1
                queue.append(source)

    ordered = sorted(
        ((n, d) for n, d in seen.items() if n != target), key=lambda kv: kv[1]
    )
    return target, ordered[:limit]


def impact_analysis(node: str) -> str:
    """What is affected if this file or symbol changes."""
    target, affected = _impact_ids(node)
    if target is None:
        return f"Could not find '{node}' in the dependency graph."
    if not affected:
        return f"Nothing depends on '{node}' — it is a leaf (safe to change in isolation)."

    graph = _graph()
    by_id = {n["id"]: n for n in graph.get("nodes", [])}
    lines = [
        f"{len(affected)} thing(s) depend on '{node}', directly or transitively:",
    ]
    for node_id, distance in affected:
        data = by_id.get(node_id, {})
        name = data.get("name", node_id)
        file_path = data.get("file_path") or (node_id if data.get("type") == "file" else "")
        where = f" ({file_path})" if file_path and data.get("type") != "file" else ""
        hop = "direct" if distance == 1 else f"{distance} hops"
        lines.append(f"- {name}{where} — {hop}")
    return "\n".join(lines)


def impact_nodes(node: str) -> list[str]:
    """Node ids affected by a change to `node`, for highlighting the graph."""
    target, affected = _impact_ids(node)
    if target is None:
        return []
    return [target, *(node_id for node_id, _ in affected)]


def generate_diagram(components: list[dict]) -> str:
    def sid(value: str) -> str:
        return re.sub(r"\W", "_", str(value)) or "n"

    lines = ["graph TD"]
    for c in components:
        if "source" in c and "target" in c:
            lines.append(f"    {sid(c['source'])} --> {sid(c['target'])}")
        elif c.get("id"):
            label = str(c.get("name", c["id"]))
            if c.get("type"):
                label += f" ({c['type']})"
            lines.append(f'    {sid(c["id"])}["{label}"]')
    return "\n".join(lines)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": "Hybrid (semantic + keyword) search over the indexed codebase. Returns matching code chunks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_content",
            "description": (
                "Read a project file with line numbers. Paths are relative to the project root. "
                "Long files are truncated to 200 lines; pass start_line/end_line to read a range."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Relative file path"},
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based, optional)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (optional)",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_definition",
            "description": "Locate where a symbol (function, class or file) is defined, with file and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol name, e.g. 'Signer' or 'serializer.py'"}
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": "Find what imports or calls a given symbol, using the dependency graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol or file name to find references to"}
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dependency_path",
            "description": "Shortest dependency path between two files or symbols, to see how they are connected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Starting file or symbol"},
                    "target": {"type": "string", "description": "Destination file or symbol"},
                },
                "required": ["source", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "impact_analysis",
            "description": (
                "What is affected if a file or symbol changes — every file that imports or calls "
                "it, directly or transitively. Use for 'what breaks if I change X' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "File path, file name, or symbol to analyse",
                    }
                },
                "required": ["node"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_diagram",
            "description": "Build a Mermaid.js graph from nodes/links. Nodes use {id,name,type}; links use {source,target}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "components": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of node or link objects",
                    }
                },
                "required": ["components"],
            },
        },
    },
]

TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "search_codebase": search_codebase,
    "get_file_content": get_file_content,
    "find_definition": find_definition,
    "find_references": find_references,
    "get_dependency_path": get_dependency_path,
    "impact_analysis": impact_analysis,
    "generate_diagram": generate_diagram,
}
