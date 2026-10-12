from __future__ import annotations

import os
import re
from collections import deque

import networkx as nx

from .tree_sitter_parser import CodeEntity

# Heuristic call detection: identifier immediately followed by "(".
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
# simple_cycles is exponential in the worst case; skip it on very large graphs.
MAX_CYCLES_NODES = 600

# Edge kinds. "contains" is structural (file -> its own entity) and must never be
# included in dependency metrics, or degree/impact figures lie.
CONTAINS = "contains"
IMPORT = "import"
CALL = "call"
DEPENDENCY_KINDS = (IMPORT, CALL)

# Extensions tried when resolving a relative JS/TS-style import to a file.
_JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")
_INDEX_NAMES = ("index.ts", "index.tsx", "index.js", "index.jsx")


class CodebaseGraphBuilder:
    def __init__(self) -> None:
        self.graph = nx.DiGraph()
        # Populated in build_graph; used by import resolution.
        self._files: set[str] = set()
        self._modules: dict[str, str] = {}
        # Paths are absolute on disk; module paths must be relative to a common root.
        self._root: str = ""

    def build_graph(self, entities: list[CodeEntity]) -> nx.DiGraph:
        self._files = {e.file_path for e in entities}
        self._root = self._common_root(self._files)
        self._modules = self._module_index(self._files)
        stems = self._stem_index(self._files)

        for e in entities:
            if not self.graph.has_node(e.file_path):
                self.graph.add_node(
                    e.file_path,
                    type="file",
                    name=os.path.basename(e.file_path),
                    external=False,
                )

            node_id = f"{e.file_path}:{e.type}:{e.name}"
            self.graph.add_node(
                node_id,
                type=e.type,
                name=e.name,
                file_path=e.file_path,
                start_line=e.start_line,
                end_line=e.end_line,
                external=False,
            )
            self.graph.add_edge(e.file_path, node_id, kind=CONTAINS, line=e.start_line)

        for e in entities:
            if e.type != "import":
                continue
            raw = self._import_name(e)
            if not raw:
                continue
            target = self._resolve_import(raw, e.file_path, stems)
            if target:
                self.graph.add_edge(
                    e.file_path, target, kind=IMPORT, line=e.start_line, resolved=True
                )
            else:
                if not self.graph.has_node(raw):
                    self.graph.add_node(raw, type="module", name=raw, external=True)
                self.graph.add_edge(e.file_path, raw, kind=IMPORT, line=e.start_line)

        self._add_call_edges(entities)
        return self.graph

    # ---------- import resolution ----------

    @staticmethod
    def _common_root(files: set[str]) -> str:
        if not files:
            return ""
        try:
            return os.path.commonpath([os.path.abspath(f) for f in files])
        except ValueError:  # mixed drives on Windows
            return ""

    def _relative(self, path: str) -> str:
        """Path relative to the project root, forward-slashed."""
        if not self._root:
            return path.replace("\\", "/")
        try:
            return os.path.relpath(os.path.abspath(path), self._root).replace("\\", "/")
        except ValueError:
            return path.replace("\\", "/")

    def _module_index(self, files: set[str]) -> dict[str, str]:
        """Map Python-style dotted module paths to files.

        `pkg/mod.py` -> "pkg.mod" and `pkg/__init__.py` -> "pkg". Paths are taken
        relative to the project root so dotted keys do not carry the temp/absolute
        prefix.
        """
        index: dict[str, str] = {}
        for path in files:
            rel = self._relative(path)
            if not rel.endswith(".py"):
                continue
            no_ext = rel[: -len(".py")]
            if no_ext.endswith("/__init__"):
                no_ext = no_ext[: -len("/__init__")]
            elif no_ext == "__init__":
                continue
            index.setdefault(no_ext.replace("/", "."), path)
        return index

    @staticmethod
    def _stem_index(files: set[str]) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for f in files:
            stem = os.path.splitext(os.path.basename(f))[0]
            index.setdefault(stem, []).append(f)
        return index

    def _resolve_import(
        self, raw: str, from_file: str, stems: dict[str, list[str]]
    ) -> str | None:
        """Resolve an import to an internal file, or None when it is external.

        Python and JS/TS get real path resolution; other languages fall back to a
        stem match, which is best-effort and can miss.
        """
        cleaned = raw.strip().strip("'\"").strip("<>")
        if not cleaned:
            return None

        ext = os.path.splitext(from_file)[1].lower()
        if ext == ".py":
            found = self._resolve_python(cleaned, from_file)
            if found or not cleaned.startswith("."):
                # An unresolvable absolute Python import is genuinely external.
                return found
            return None
        if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
            if cleaned.startswith("."):
                return self._resolve_relative_js(cleaned, from_file)
            return None  # bare specifier -> node_modules / external

        # Fallback for other languages: stem match, preferring the same directory.
        candidate = re.split(r"[./\\:]", cleaned)[-1]
        for key in (cleaned, candidate):
            paths = stems.get(key)
            if not paths:
                continue
            same_dir = [p for p in paths if os.path.dirname(p) == os.path.dirname(from_file)]
            for path in same_dir or paths:
                if path != from_file:
                    return path
        return None

    def _resolve_python(self, raw: str, from_file: str) -> str | None:
        rel_from = self._relative(from_file)
        if raw.startswith("."):
            # For `pkg/mod.py`, a relative import is anchored at the file's package
            # (pkg); each extra leading dot walks one package further up.
            level = len(raw) - len(raw.lstrip("."))
            remainder = raw[level:]
            module_path = rel_from[: -len(".py")] if rel_from.endswith(".py") else rel_from
            if module_path.endswith("/__init__"):
                package = module_path[: -len("/__init__")]
            else:
                package = os.path.dirname(module_path)
            package_parts = [p for p in package.split("/") if p]
            up = level - 1
            base_parts = package_parts[: len(package_parts) - up] if up else package_parts
            cand_parts = base_parts + [p for p in remainder.split(".") if p]
        else:
            # Absolute import: try the path as written, then as a suffix of the
            # project (src/ layouts put packages below a source root).
            cand_parts = [p for p in raw.split(".") if p]

        for cut in range(len(cand_parts), 0, -1):
            module = ".".join(cand_parts[:cut])
            found = self._modules.get(module)
            if found and found != from_file:
                return found
            # Suffix match: "pkg.mod" also matches "src.pkg.mod".
            if not raw.startswith("."):
                for key, path in self._modules.items():
                    if key.endswith(f".{module}") and path != from_file:
                        return path
        return None

    def _resolve_relative_js(self, raw: str, from_file: str) -> str | None:
        target = os.path.normpath(os.path.join(os.path.dirname(from_file), raw))
        candidates = [target]
        candidates += [target + suffix for suffix in _JS_SUFFIXES]
        candidates += [os.path.join(target, name) for name in _INDEX_NAMES]
        for candidate in candidates:
            normalized = candidate.replace("\\", "/")
            if normalized in self._files and normalized != from_file:
                return normalized
        return None

    @staticmethod
    def _import_name(e: CodeEntity) -> str | None:
        """Module reference for an import entity.

        Parses the statement text rather than trusting the captured node name, so
        Python relative imports keep their leading dots (`from .mod import x` ->
        `.mod`) and `from . import mod` becomes `.mod`.
        """
        text = e.code_snippet.strip()

        m = re.match(r"from\s+([.\w]+)\s+import\b", text)
        if m:
            module = m.group(1)
            if module == "." or module.endswith("."):
                # `from . import helper` — the imported name is the submodule.
                tail = text.split("import", 1)[1].strip().lstrip("(")
                first = re.split(r"[,\s]", tail)[0]
                if first and first not in ("*", ""):
                    module = f"{module}{first}"
            return module

        m = re.match(r"import\s+([\w.]+)", text)
        if m:
            return m.group(1)

        # JS/TS styles.
        m = re.search(r"""from\s+['"]([^'"]+)['"]""", text)
        if m:
            return m.group(1)
        m = re.search(r"""require\(\s*['"]([^'"]+)['"]\s*\)""", text)
        if m:
            return m.group(1)

        return e.name or None

    def _add_call_edges(self, entities: list[CodeEntity]) -> None:
        functions = [e for e in entities if e.type == "function"]
        by_name: dict[str, list[str]] = {}
        for e in functions:
            by_name.setdefault(e.name, []).append(f"{e.file_path}:{e.type}:{e.name}")

        for e in functions:
            source = f"{e.file_path}:{e.type}:{e.name}"
            for match in CALL_RE.finditer(e.code_snippet):
                name = match.group(1)
                if name == e.name:
                    continue
                # Line of the call site, relative to the function's start.
                line = e.start_line + e.code_snippet[: match.start()].count("\n")
                for target in by_name.get(name, []):
                    if target != source and not self.graph.has_edge(source, target):
                        # ponytail: name-based, no scope resolution — same-named
                        # functions in different modules both link. Refine with real
                        # binding resolution if it ever becomes noisy.
                        self.graph.add_edge(source, target, kind=CALL, line=line)

    # ---------- queries ----------

    def _dependency_edges(self, node: str, direction: str) -> list[tuple[str, str, dict]]:
        """Edges of a dependency kind, ignoring structural containment."""
        edges = (
            self.graph.out_edges(node, data=True)
            if direction == "out"
            else self.graph.in_edges(node, data=True)
        )
        return [(u, v, d) for u, v, d in edges if d.get("kind") in DEPENDENCY_KINDS]

    def impact(self, node: str, limit: int = 40) -> dict:
        """Reverse-dependency closure: everything that would be affected by a change.

        Traversal follows dependency edges backwards (who imports/calls this), so
        structural containment edges never inflate the result.
        """
        if node not in self.graph:
            return {"node": node, "found": False, "affected": [], "total": 0}

        seen: dict[str, int] = {node: 0}
        queue: deque[str] = deque([node])
        while queue:
            current = queue.popleft()
            for source, _target, _data in self._dependency_edges(current, "in"):
                if source not in seen:
                    seen[source] = seen[current] + 1
                    queue.append(source)

        affected = []
        for other, distance in sorted(seen.items(), key=lambda kv: kv[1]):
            if other == node:
                continue
            data = self.graph.nodes[other]
            affected.append(
                {
                    "id": other,
                    "name": data.get("name", other),
                    "type": data.get("type", "unknown"),
                    "file_path": data.get("file_path", other if data.get("type") == "file" else ""),
                    "distance": distance,
                }
            )
        return {
            "node": node,
            "found": True,
            "total": len(affected),
            "affected": affected[:limit],
        }

    def export_d3_json(self) -> dict:
        return {
            "nodes": [{"id": n, **d} for n, d in self.graph.nodes(data=True)],
            "links": [
                {"source": s, "target": t, **d}
                for s, t, d in self.graph.edges(data=True)
            ],
        }

    def insights(self, limit: int = 8) -> dict:
        file_nodes = [
            n for n, d in self.graph.nodes(data=True) if d.get("type") == "file"
        ]
        # Dependency-only view: containment edges are excluded so degrees are honest.
        deps = nx.DiGraph()
        deps.add_nodes_from(file_nodes)
        for source, target, data in self.graph.edges(data=True):
            if data.get("kind") == IMPORT and source in deps and target in deps:
                deps.add_edge(source, target)

        in_degree = dict(deps.in_degree())

        cycles: list[list[str]] = []
        if len(file_nodes) <= MAX_CYCLES_NODES:
            try:
                cycles = [list(c) for c in nx.simple_cycles(deps)][:limit]
            except nx.NetworkXError:
                cycles = []

        hubs = [
            {
                "id": node,
                "name": os.path.basename(node),
                "imported_by": in_degree.get(node, 0),
            }
            for node in sorted(file_nodes, key=lambda n: in_degree.get(n, 0), reverse=True)
            if in_degree.get(node, 0)
        ][:limit]

        counts: dict[str, int] = {}
        for _, data in self.graph.nodes(data=True):
            node_type = data.get("type", "unknown")
            counts[node_type] = counts.get(node_type, 0) + 1

        unresolved = sum(
            1
            for _, _, data in self.graph.edges(data=True)
            if data.get("kind") == IMPORT and not data.get("resolved")
        )

        return {
            "nodes": self.graph.number_of_nodes(),
            "links": self.graph.number_of_edges(),
            "entity_counts": counts,
            "cycles": cycles,
            "hubs": hubs,
            "unresolved_imports": unresolved,
        }
