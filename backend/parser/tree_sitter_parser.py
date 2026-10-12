from __future__ import annotations

import os
import warnings
from dataclasses import dataclass

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Query, QueryCursor

try:
    from tree_sitter_language_pack import get_language as _get_language
except ImportError:  # pragma: no cover - the pack is a hard requirement
    _get_language = None

_PY = Language(tspython.language())
_JS = Language(tsjavascript.language())

_pack_cache: dict[str, Language] = {}
_missing_grammars: set[str] = set()


def _pack(name: str) -> Language | None:
    """Load a grammar from tree-sitter-language-pack, cached so extensions that
    share a language (e.g. .c/.h) reuse the same Language object.

    Returns None when the grammar is unavailable, and records the name so a caller
    can tell "grammar absent" apart from "file has no entities". The previous code
    fell back to the JavaScript grammar for TypeScript; that parsed `.ts` with
    syntax errors, and parse_file's has_error guard then dropped every entity
    silently — the whole file looked empty instead of unparsed.
    """
    if _get_language is None:
        _missing_grammars.add(name)
        return None
    if name not in _pack_cache:
        try:
            _pack_cache[name] = _get_language(name)
        except Exception:  # noqa: BLE001 - a missing grammar must not break import
            _missing_grammars.add(name)
            return None
    return _pack_cache.get(name)


_TS = _pack("typescript")
_TSX = _pack("tsx")


# ext -> (language name, grammar). A grammar of None means the extension is skipped.
_EXTENSIONS: dict[str, tuple[str, Language | None]] = {
    ".py": ("python", _PY),
    ".js": ("javascript", _JS),
    ".jsx": ("javascript", _JS),
    ".mjs": ("javascript", _JS),
    ".cjs": ("javascript", _JS),
    ".ts": ("typescript", _TS),
    ".tsx": ("tsx", _TSX),
    ".java": ("java", _pack("java")),
    ".go": ("go", _pack("go")),
    ".rs": ("rust", _pack("rust")),
    ".c": ("c", _pack("c")),
    ".h": ("c", _pack("c")),
    ".cpp": ("cpp", _pack("cpp")),
    ".cc": ("cpp", _pack("cpp")),
    ".cxx": ("cpp", _pack("cpp")),
    ".hpp": ("cpp", _pack("cpp")),
    ".hh": ("cpp", _pack("cpp")),
}

if _missing_grammars:
    # Loud, not silent: a missing grammar means whole languages vanish from the
    # index. The previous silent fallback hid exactly this for TypeScript.
    warnings.warn(
        "tree-sitter grammars unavailable, files using them will not be parsed: "
        + ", ".join(sorted(_missing_grammars)),
        RuntimeWarning,
        stacklevel=2,
    )

# (entity_type, query) per language. @name is optional; @def is the entity node.
_QUERY_SOURCE: dict[str, list[tuple[str, str]]] = {
    "python": [
        ("function", "(function_definition name: (identifier) @name) @def"),
        ("class", "(class_definition name: (identifier) @name) @def"),
        ("import", "(import_statement name: (dotted_name) @name) @def"),
        # Relative imports (`from . import x`, `from .mod import y`) hang off a
        # relative_import node, not dotted_name — matching dotted_name alone silently
        # dropped every relative import. Match the statement and let the graph
        # builder parse the text.
        ("import", "(import_from_statement) @def"),
    ],
    "javascript": [
        ("function", "(function_declaration name: (identifier) @name) @def"),
        ("class", "(class_declaration name: (identifier) @name) @def"),
        (
            "function",
            "(variable_declarator name: (identifier) @name value: (arrow_function)) @def",
        ),
        ("import", "(import_statement source: (string) @name) @def"),
    ],
    "java": [
        ("class", "(class_declaration name: (identifier) @name) @def"),
        ("class", "(interface_declaration name: (identifier) @name) @def"),
        ("function", "(method_declaration name: (identifier) @name) @def"),
        ("function", "(constructor_declaration name: (identifier) @name) @def"),
        ("import", "(import_declaration (scoped_identifier) @name) @def"),
    ],
    "go": [
        ("function", "(function_declaration name: (identifier) @name) @def"),
        ("function", "(method_declaration name: (field_identifier) @name) @def"),
        (
            "class",
            "(type_declaration (type_spec name: (type_identifier) @name)) @def",
        ),
        ("import", "(import_spec path: (interpreted_string_literal) @name) @def"),
    ],
    "rust": [
        ("function", "(function_item name: (identifier) @name) @def"),
        ("class", "(struct_item name: (type_identifier) @name) @def"),
        ("class", "(enum_item name: (type_identifier) @name) @def"),
        ("class", "(trait_item name: (type_identifier) @name) @def"),
        ("class", "(impl_item type: (type_identifier) @name) @def"),
        ("import", "(use_declaration argument: (_) @name) @def"),
    ],
    "c": [
        (
            "function",
            "(function_definition declarator: (function_declarator "
            "declarator: (identifier) @name)) @def",
        ),
        ("class", "(struct_specifier name: (type_identifier) @name) @def"),
        ("import", "(preproc_include path: (string_literal) @name) @def"),
        ("import", "(preproc_include path: (system_lib_string) @name) @def"),
    ],
    "cpp": [
        (
            "function",
            "(function_definition declarator: (function_declarator "
            "declarator: (identifier) @name)) @def",
        ),
        ("class", "(class_specifier name: (type_identifier) @name) @def"),
        ("class", "(struct_specifier name: (type_identifier) @name) @def"),
        ("import", "(preproc_include path: (string_literal) @name) @def"),
        ("import", "(preproc_include path: (system_lib_string) @name) @def"),
    ],
}
# TypeScript needs its own queries, not the JavaScript ones: `class_declaration`'s
# name is a `type_identifier` there, so the JS class query is rejected outright,
# and interfaces/type aliases are TypeScript-only. Both were missing entirely.
_TS_QUERIES = [
    ("function", "(function_declaration name: (identifier) @name) @def"),
    ("class", "(class_declaration name: (type_identifier) @name) @def"),
    ("class", "(interface_declaration name: (type_identifier) @name) @def"),
    ("class", "(type_alias_declaration name: (type_identifier) @name) @def"),
    (
        "function",
        "(variable_declarator name: (identifier) @name value: (arrow_function)) @def",
    ),
    ("import", "(import_statement source: (string) @name) @def"),
]
_QUERY_SOURCE["typescript"] = _TS_QUERIES
_QUERY_SOURCE["tsx"] = _TS_QUERIES


@dataclass
class CodeEntity:
    name: str
    type: str
    start_line: int
    end_line: int
    code_snippet: str
    file_path: str


class MultiLanguageParser:
    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}
        self._queries: dict[str, list[tuple[str, Query]]] = {}
        unique = {
            name: lang for name, lang in _EXTENSIONS.values() if lang is not None
        }
        for name, lang in unique.items():
            self._parsers[name] = Parser(lang)
            compiled: list[tuple[str, Query]] = []
            for etype, source in _QUERY_SOURCE.get(name, []):
                try:
                    compiled.append((etype, Query(lang, source)))
                except Exception as exc:  # noqa: BLE001 - one bad pattern must not kill the parser
                    warnings.warn(
                        f"tree-sitter query rejected by the {name} grammar ({etype}): {exc}",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
            self._queries[name] = compiled

    def parse_file(self, filepath: str) -> list[CodeEntity]:
        entry = _EXTENSIONS.get(os.path.splitext(filepath)[1].lower())
        if entry is None:
            return []
        name, lang = entry
        if lang is None or name not in self._parsers:
            return []

        with open(filepath, "rb") as fh:
            source = fh.read()

        tree = self._parsers[name].parse(source)
        entities: list[CodeEntity] = []
        for etype, query in self._queries[name]:
            for _, captures in QueryCursor(query).matches(tree.root_node):
                node = captures.get("def", [None])[0]
                if node is None or node.has_error:
                    continue
                entities.append(
                    CodeEntity(
                        name=self._name(captures.get("name"), node, source),
                        type=etype,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        code_snippet=source[node.start_byte : node.end_byte].decode(
                            "utf-8", "replace"
                        ),
                        file_path=filepath,
                    )
                )
        return sorted(entities, key=lambda e: e.start_line)

    @staticmethod
    def _name(captures, node, source: bytes) -> str:
        if captures:
            text = source[captures[0].start_byte : captures[0].end_byte].decode(
                "utf-8", "replace"
            )
            return text.strip().strip("'\"")
        return source[node.start_byte : node.end_byte].decode(
            "utf-8", "replace"
        ).splitlines()[0].strip()


if __name__ == "__main__":
    import tempfile

    SAMPLES = {
        ".py": "import os\nfrom a.b import c\ndef foo(x):\n    return x\nclass Bar:\n    pass\n",
        ".js": "import x from 'y';\nfunction foo(){}\nclass Bar {}\n",
        ".java": "import java.util.List;\nclass Bar { void foo() {} }\n",
        ".go": 'package main\nimport "fmt"\nfunc Foo() {}\ntype Bar struct{}\n',
        ".rs": "use std::io;\nfn foo() {}\nstruct Bar;\n",
        ".c": '#include <stdio.h>\nstruct Bar { int x; };\nint foo(void) { return 0; }\n',
        ".cpp": '#include <vector>\nclass Bar {};\nint foo() { return 0; }\n',
    }
    parser = MultiLanguageParser()
    ok = True
    for ext, src in SAMPLES.items():
        with tempfile.NamedTemporaryFile("w", suffix=ext, delete=False) as f:
            f.write(src)
            path = f.name
        got = parser.parse_file(path)
        os.unlink(path)
        names = sorted({(e.type, e.name) for e in got})
        status = "ok " if got else "EMPTY"
        if not got:
            ok = False
        print(f"{status} {ext:5} -> {names}")
    print("all languages produced entities" if ok else "SOME LANGUAGES EMPTY")
