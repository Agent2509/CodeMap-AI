from __future__ import annotations

from typing import Any

from rag.retriever import CodebaseRetriever, _BM25


class _StubCollection:
    """Minimal Chroma stand-in so search() can be tested without the embedding model."""

    def __init__(
        self,
        docs: list[str],
        metas: list[dict[str, Any]],
        vector_hits: list[int] | None = None,
    ) -> None:
        self._docs = docs
        self._metas = metas
        self._vector_hits = vector_hits if vector_hits is not None else list(range(len(docs)))

    def count(self) -> int:
        return len(self._docs)

    def get(self, include: list[str]) -> dict[str, Any]:
        return {"documents": self._docs, "metadatas": self._metas}

    def query(self, query_texts: list[str], n_results: int, include: list[str]) -> dict[str, Any]:
        idx = self._vector_hits[:n_results]
        return {
            "documents": [[self._docs[i] for i in idx]],
            "metadatas": [[self._metas[i] for i in idx]],
        }


def _retriever_with(
    docs: list[str], metas: list[dict[str, Any]], vector_hits: list[int] | None = None
) -> CodebaseRetriever:
    retriever = CodebaseRetriever.__new__(CodebaseRetriever)  # skip __init__ (no Chroma)
    retriever.collection = _StubCollection(docs, metas, vector_hits)  # type: ignore[assignment]
    retriever._corpus = None
    retriever._metadatas = []
    retriever._bm25 = None
    return retriever


def test_search_returns_one_chunk_per_file() -> None:
    docs = ["a1", "a2", "a3", "b1", "c1"]
    metas = [
        {"file_path": "a.py"},
        {"file_path": "a.py"},
        {"file_path": "a.py"},
        {"file_path": "b.py"},
        {"file_path": "c.py"},
    ]

    out = _retriever_with(docs, metas).search("q", top_k=3)

    assert out == ["a1", "b1", "c1"]


def test_search_on_empty_collection_returns_nothing() -> None:
    assert _retriever_with([], []).search("q") == []


def test_lexical_match_surfaces_a_chunk_vector_missed() -> None:
    docs = ["alpha body", "beta body", "def gamma_secret(): pass"]
    metas = [
        {"file_path": "a.py"},
        {"file_path": "b.py"},
        {"file_path": "c.py"},
    ]
    retriever = _retriever_with(docs, metas, vector_hits=[0, 1])

    out = retriever.search("gamma_secret", top_k=3)

    assert any("gamma_secret" in d for d in out)


def test_bm25_ranks_the_term_match_highest() -> None:
    index = _BM25(["unrelated text", "unique_token appears here", "other"])
    scores = index.scores("unique_token")

    assert scores[1] > scores[0]
    assert scores[1] > scores[2]
