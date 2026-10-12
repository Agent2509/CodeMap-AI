"""Retrieval eval: does a real question about this repo return the right file?

Measures the *pipeline*, not the embedding model — the vector side is stubbed out
via _retriever_with, so this stays offline and instant. Two consequences:

  * coverage (does a file produce chunks at all) is measured exactly;
  * retrieval is measured *lexically*. A natural-language question that only the
    embedding model could rank correctly is NOT covered here — the cases below use
    wording that shares real tokens with the target file, which is how a symbol or
    config lookup behaves in practice.

The corpus is this repository, built through the same _iter_files /
_parse_and_chunk seam the indexer uses, so the harness cannot drift from
production chunking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from parser.chunker import AstSemanticChunker
from parser.tree_sitter_parser import MultiLanguageParser
from routers.upload import HEADER_RE, _iter_files, _parse_and_chunk
from test_retriever import _retriever_with

REPO_ROOT = Path(__file__).resolve().parents[2]

# Questions about source code. All of these pass today.
_CODE_CASES = [
    ("where is the git clone url validated", "routers/upload.py"),
    ("how are code chunks split for the vector store", "parser/chunker.py"),
    ("bm25 ranking of lexical results", "rag/retriever.py"),
    ("the react agent loop and tool calls", "agent/react_agent.py"),
    ("path traversal blocked", "agent/tools.py"),
    ("impact analysis blast radius reverse dependency", "agent/tools.py"),
    ("build the dependency graph from entities", "parser/graph_builder.py"),
]

# Docs and config, which had no chunks at all before the fallback chunker.
_TEXT_CASES = [
    ("uvicorn reload port backend", "README.md"),
    ("react-dom lucide-react dependencies", "frontend/package.json"),
    ("NEXT_PUBLIC_API_URL localhost", "frontend/src/lib/api.ts"),
    ("compilerOptions jsx moduleResolution", "frontend/tsconfig.json"),
    ("PRESENTATION demo architecture", "PRESENTATION.md"),
]

# These produced zero chunks before the fallback chunker existed, so a regression
# there silently makes them unsearchable again.
_MUST_BE_INDEXED = [
    "README.md",
    "PRESENTATION.md",
    "AGENTS.md",
    "frontend/package.json",
    "frontend/tsconfig.json",
    "frontend/src/lib/api.ts",
    "frontend/src/app/globals.css",
    "backend/requirements.txt",
]


def _repo_corpus() -> tuple[list[str], list[dict[str, Any]]]:
    parser, chunker = MultiLanguageParser(), AstSemanticChunker()
    docs: list[str] = []
    metas: list[dict[str, Any]] = []
    for path in _iter_files(REPO_ROOT):
        _, chunks = _parse_and_chunk(path, parser, chunker)
        relative = path.relative_to(REPO_ROOT).as_posix()
        for chunk in chunks:
            # Production takes file_path from the chunk header; use the same source.
            header = HEADER_RE.match(chunk)
            docs.append(chunk)
            metas.append({"file_path": header.group(1) if header else relative})
    return docs, metas


@pytest.fixture(scope="module")
def corpus() -> tuple[list[str], list[dict[str, Any]]]:
    docs, metas = _repo_corpus()
    assert docs, "expected the repo to produce chunks"
    return docs, metas


@pytest.fixture(scope="module")
def repo_search(corpus: tuple[list[str], list[dict[str, Any]]]) -> Callable[..., list[str]]:
    docs, metas = corpus
    retriever = _retriever_with(docs, metas, vector_hits=[])

    doc_file: dict[str, str] = {}
    for doc, meta in zip(docs, metas):
        doc_file.setdefault(doc, meta["file_path"])

    def search(query: str, top_k: int = 5) -> list[str]:
        return [doc_file.get(doc, "") for doc in retriever.search(query, top_k=top_k)]

    return search


def _hits(search: Callable[..., list[str]], query: str, expected: str) -> bool:
    return any(Path(f).as_posix().endswith(expected) for f in search(query))


@pytest.mark.parametrize("query,expected", _CODE_CASES)
def test_code_queries_find_their_file(
    repo_search: Callable[..., list[str]], query: str, expected: str
) -> None:
    assert _hits(repo_search, query, expected), f"top hits: {repo_search(query)}"


@pytest.mark.parametrize("query,expected", _TEXT_CASES)
def test_text_queries_find_their_file(
    repo_search: Callable[..., list[str]], query: str, expected: str
) -> None:
    assert _hits(repo_search, query, expected), f"top hits: {repo_search(query)}"


@pytest.mark.parametrize("relative", _MUST_BE_INDEXED)
def test_non_ast_file_is_indexed(
    corpus: tuple[list[str], list[dict[str, Any]]], relative: str
) -> None:
    """Files the AST pass ignores must still reach the vector store."""
    _, metas = corpus
    indexed = {Path(m["file_path"]).name for m in metas if Path(m["file_path"]).as_posix().endswith(relative)}
    assert indexed, f"{relative} produced no chunks"
