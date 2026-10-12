from __future__ import annotations

from pathlib import Path

from parser.graph_builder import CALL, CONTAINS, IMPORT, CodebaseGraphBuilder
from parser.tree_sitter_parser import CodeEntity, MultiLanguageParser


def entity(name: str, etype: str, path: str, snippet: str = "") -> CodeEntity:
    return CodeEntity(
        name=name,
        type=etype,
        start_line=1,
        end_line=2,
        code_snippet=snippet,
        file_path=path,
    )


def parse_all(root: Path) -> list[CodeEntity]:
    parser = MultiLanguageParser()
    entities: list[CodeEntity] = []
    for path in sorted(root.rglob("*.py")):
        entities.extend(parser.parse_file(str(path)))
    return entities


def write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def test_build_graph_and_export() -> None:
    builder = CodebaseGraphBuilder()
    builder.build_graph(
        [
            entity("foo", "function", "a.py", "def foo(): pass"),
            entity("Bar", "class", "a.py", "class Bar: pass"),
        ]
    )
    data = builder.export_d3_json()
    ids = {n["id"] for n in data["nodes"]}
    assert "a.py" in ids
    assert "a.py:function:foo" in ids
    assert "a.py:class:Bar" in ids
    assert any(
        link["source"] == "a.py" and link["target"] == "a.py:function:foo"
        for link in data["links"]
    )


def test_import_creates_module_node() -> None:
    builder = CodebaseGraphBuilder()
    builder.build_graph([entity("os", "import", "a.py", "import os")])
    data = builder.export_d3_json()
    modules = {n["id"] for n in data["nodes"] if n.get("type") == "module"}
    assert "os" in modules


def test_import_to_internal_file_becomes_file_edge() -> None:
    builder = CodebaseGraphBuilder()
    builder.build_graph(
        [
            entity("helper", "function", "pkg/a.py", "def helper(): pass"),
            entity("a", "import", "pkg/b.py", "import a"),
        ]
    )
    assert builder.graph.has_edge("pkg/b.py", "pkg/a.py")


def test_external_modules_are_marked() -> None:
    builder = CodebaseGraphBuilder()
    builder.build_graph([entity("os", "import", "a.py", "import os")])
    assert builder.graph.nodes["os"]["external"] is True
    assert builder.graph.nodes["a.py"]["external"] is False


def test_call_edges_between_functions() -> None:
    builder = CodebaseGraphBuilder()
    builder.build_graph(
        [
            entity("helper", "function", "a.py", "def helper(): pass"),
            entity("main", "function", "b.py", "def main(): helper()"),
        ]
    )
    assert builder.graph.has_edge("b.py:function:main", "a.py:function:helper")


def test_insights_reports_hubs() -> None:
    builder = CodebaseGraphBuilder()
    builder.build_graph(
        [
            entity("core", "function", "core.py", "def core(): pass"),
            entity("core", "import", "one.py", "import core"),
            entity("core", "import", "two.py", "import core"),
        ]
    )
    insights = builder.insights()
    hub_ids = {h["id"] for h in insights["hubs"]}
    assert "core.py" in hub_ids
    assert insights["entity_counts"]["file"] == 3


def test_edges_are_typed() -> None:
    builder = CodebaseGraphBuilder()
    builder.build_graph([entity("foo", "function", "a.py", "def foo(): pass")])
    kinds = {d.get("kind") for _, _, d in builder.graph.edges(data=True)}
    assert kinds == {CONTAINS}


def test_contains_edges_are_not_dependencies() -> None:
    builder = CodebaseGraphBuilder()
    builder.build_graph([entity("foo", "function", "a.py", "def foo(): pass")])
    # A file containing its own function must not read as something depending on it.
    assert builder.impact("a.py")["total"] == 0


def test_relative_python_import_resolves(tmp_path: Path) -> None:
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/helper.py", "def thing():\n    return 1\n")
    write(tmp_path, "pkg/mod.py", "from .helper import thing\nfrom . import helper\n")

    builder = CodebaseGraphBuilder()
    builder.build_graph(parse_all(tmp_path))

    targets = {
        v
        for u, v, d in builder.graph.edges(data=True)
        if d.get("kind") == IMPORT and u.endswith("pkg/mod.py")
    }
    assert any(t.endswith("pkg/helper.py") for t in targets), targets


def test_absolute_python_import_resolves(tmp_path: Path) -> None:
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/mod.py", "def run():\n    return 1\n")
    write(tmp_path, "app.py", "from pkg.mod import run\n")

    builder = CodebaseGraphBuilder()
    builder.build_graph(parse_all(tmp_path))

    assert any(
        u.endswith("app.py") and v.endswith("pkg/mod.py")
        for u, v, d in builder.graph.edges(data=True)
        if d.get("kind") == IMPORT
    )


def test_external_import_is_not_resolved(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "import os\n")
    builder = CodebaseGraphBuilder()
    builder.build_graph(parse_all(tmp_path))
    edge = next(d for _, _, d in builder.graph.edges(data=True) if d.get("kind") == IMPORT)
    assert not edge.get("resolved")


def test_impact_is_transitive(tmp_path: Path) -> None:
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/helper.py", "def thing():\n    return 1\n")
    write(tmp_path, "pkg/mod.py", "from .helper import thing\n")
    write(tmp_path, "app.py", "from pkg.mod import thing\n")

    builder = CodebaseGraphBuilder()
    builder.build_graph(parse_all(tmp_path))

    helper = next(f for f in builder._files if f.endswith("pkg/helper.py"))
    affected = builder.impact(helper)
    names = {Path(a["id"]).name for a in affected["affected"]}

    assert "mod.py" in names
    assert "app.py" in names  # reached through mod.py, so traversal is transitive
    assert affected["total"] >= 2
