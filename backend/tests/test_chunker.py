from __future__ import annotations

from pathlib import Path

from parser.chunker import MAX_CHUNK_CHARS, AstSemanticChunker
from parser.tree_sitter_parser import CodeEntity
from routers.upload import HEADER_RE


def chunker() -> AstSemanticChunker:
    return AstSemanticChunker()


def test_header_prefixed() -> None:
    ent = CodeEntity(
        name="f",
        type="function",
        start_line=1,
        end_line=2,
        code_snippet="def f():\n    pass\n",
        file_path="a.py",
    )
    chunks = chunker().chunk_entities([ent])
    assert len(chunks) == 1
    assert chunks[0].startswith("# File: a.py | Type: function | Name: f | Lines: 1-2\n")


def test_large_snippet_split() -> None:
    ent = CodeEntity(
        name="g",
        type="function",
        start_line=1,
        end_line=2,
        code_snippet="x = 1\n" * 1000,
        file_path="a.py",
    )
    assert len(chunker().chunk_entities([ent])) > 1


def test_chunk_file_prefers_entities_when_present(tmp_path: Path) -> None:
    ent = CodeEntity(
        name="f",
        type="function",
        start_line=1,
        end_line=2,
        code_snippet="def f():\n    pass\n",
        file_path="a.py",
    )
    path = tmp_path / "a.py"
    path.write_text("def f():\n    pass\n")

    chunks = chunker().chunk_file(path, [ent])

    assert len(chunks) == 1
    assert "| Type: function |" in chunks[0]


def test_chunk_file_falls_back_to_text_windows(tmp_path: Path) -> None:
    # Regression: a file with no AST entities produced no chunks at all, so every
    # doc and config file was invisible to search.
    path = tmp_path / "README.md"
    path.write_text("# Setup\n\nRun the backend with uvicorn on port 8000.\n")

    chunks = chunker().chunk_file(path, [])

    assert len(chunks) == 1
    assert "| Type: text |" in chunks[0]
    assert "uvicorn on port 8000" in chunks[0]


def test_chunk_file_windows_long_text(tmp_path: Path) -> None:
    path = tmp_path / "long.md"
    path.write_text("".join(f"line {i}\n" for i in range(150)))

    chunks = chunker().chunk_file(path, [])

    assert len(chunks) == 3  # 150 lines at 60 per window
    assert all("| Lines:" in c for c in chunks)


def test_chunk_file_skips_oversized_files(tmp_path: Path) -> None:
    path = tmp_path / "package-lock.json"
    path.write_text('{"a": 1}\n' * 10_000)  # > MAX_FALLBACK_BYTES

    assert chunker().chunk_file(path, []) == []


def test_chunk_file_skips_binary(tmp_path: Path) -> None:
    path = tmp_path / "logo.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")

    assert chunker().chunk_file(path, []) == []


def test_chunk_file_skips_near_empty(tmp_path: Path) -> None:
    path = tmp_path / "__init__.py"
    path.write_text("")

    assert chunker().chunk_file(path, []) == []


def test_chunks_are_bounded_even_with_huge_lines(tmp_path: Path) -> None:
    # Regression: a 60-line window of very long lines produced a ~29k char chunk,
    # which padded the embedding batch and blew up the model's attention matrix.
    path = tmp_path / "minified.js"
    path.write_text("const a=1;" * 5000)  # ~50k chars on a single line (< size cap)

    chunks = chunker().chunk_file(path, [])

    assert chunks
    assert max(len(c) for c in chunks) <= MAX_CHUNK_CHARS + 200  # header allowance


def test_large_entity_snippet_is_also_bounded() -> None:
    ent = CodeEntity(
        name="g",
        type="function",
        start_line=1,
        end_line=2,
        code_snippet="x = 1\n" * 2000,
        file_path="a.py",
    )
    chunks = chunker().chunk_entities([ent])

    assert max(len(c) for c in chunks) <= MAX_CHUNK_CHARS + 200


def test_header_sanitises_pipe_so_metadata_still_parses() -> None:
    ent = CodeEntity(
        name="a | b",
        type="function",
        start_line=1,
        end_line=1,
        code_snippet="pass\n",
        file_path="a.py",
    )
    header = chunker().chunk_entities([ent])[0].splitlines()[0]

    # HEADER_RE must still match, or the chunk loses its file_path/name.
    assert HEADER_RE.match(header + "\n")
    assert header.count("|") == 3
