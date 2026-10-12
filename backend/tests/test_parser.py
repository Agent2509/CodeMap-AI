from __future__ import annotations

from pathlib import Path

from parser.tree_sitter_parser import MultiLanguageParser


def test_parse_python_entities(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text(
        "import os\n"
        "from a.b import c\n\n"
        "def foo(x):\n"
        "    return x\n\n"
        "class Bar:\n"
        "    def method(self):\n"
        "        pass\n"
    )
    entities = MultiLanguageParser().parse_file(str(source))
    found = {(e.type, e.name) for e in entities}
    assert ("function", "foo") in found
    assert ("class", "Bar") in found
    assert ("import", "os") in found
    for e in entities:
        assert e.start_line >= 1
        assert e.end_line >= e.start_line
        assert e.code_snippet


def test_unknown_extension_returns_empty(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("hello")
    assert MultiLanguageParser().parse_file(str(target)) == []


_OTHER_LANGUAGES = {
    "Module.java": ("import java.util.List;\nclass Bar { void foo() {} }\n", "class", "Bar"),
    "main.go": ('package main\nimport "fmt"\nfunc Foo() {}\ntype Bar struct{}\n', "function", "Foo"),
    "lib.rs": ("use std::io;\nfn foo() {}\nstruct Bar;\n", "function", "foo"),
    "lib.c": ('#include <stdio.h>\nstruct Bar { int x; };\nint foo(void) { return 0; }\n', "function", "foo"),
    "lib.cpp": ("#include <vector>\nclass Bar {};\nint foo() { return 0; }\n", "class", "Bar"),
}


def test_parses_each_supported_language(tmp_path: Path) -> None:
    parser = MultiLanguageParser()
    for filename, (source, etype, name) in _OTHER_LANGUAGES.items():
        path = tmp_path / filename
        path.write_text(source)
        entities = parser.parse_file(str(path))
        found = {(e.type, e.name) for e in entities}
        assert (etype, name) in found, (filename, found)


_TS_SOURCE = (
    "export interface Node { id: string }\n"
    "export type Alias = string;\n"
    "export const linkKey = (l: Node): string => l.id;\n"
    "export class Graph {}\n"
    "export function neighborsOf(id: string): Set<string> { return new Set([id]); }\n"
)


def test_parses_typescript_entities(tmp_path: Path) -> None:
    # Regression: `.ts` was parsed with the JavaScript grammar, which errored on
    # type annotations and dropped every entity — real TS source looked empty.
    path = tmp_path / "graph.ts"
    path.write_text(_TS_SOURCE)

    found = {(e.type, e.name) for e in MultiLanguageParser().parse_file(str(path))}

    assert ("class", "Node") in found  # interface
    assert ("class", "Alias") in found  # type alias
    assert ("class", "Graph") in found
    assert ("function", "linkKey") in found  # typed arrow function
    assert ("function", "neighborsOf") in found


def test_parses_tsx_entities(tmp_path: Path) -> None:
    path = tmp_path / "Panel.tsx"
    path.write_text(_TS_SOURCE + "export const Panel = () => null;\n")

    found = {(e.type, e.name) for e in MultiLanguageParser().parse_file(str(path))}

    assert ("function", "Panel") in found
