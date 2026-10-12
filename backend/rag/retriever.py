from __future__ import annotations

import math
import re
import threading
from collections import Counter

from chromadb.api.types import Metadata

from .embeddings import get_chroma_client, get_embedding_function

COLLECTION_NAME = "codebase"

_TOKEN_RE = re.compile(r"[A-Za-z_]\w*")
# Reciprocal-rank-fusion constant. Blends vector and lexical results without
# needing their scores on the same scale.
RRF_K = 60


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class _BM25:
    """Compact Okapi BM25 over the collection's chunks."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._term_freqs = [Counter(_tokenize(d)) for d in docs]
        self._lengths = [sum(tf.values()) for tf in self._term_freqs]
        self._n = len(docs)
        self._avgdl = (sum(self._lengths) / self._n) if self._n else 0.0
        self._df: Counter[str] = Counter()
        for tf in self._term_freqs:
            self._df.update(tf.keys())

    def scores(self, query: str) -> list[float]:
        scores = [0.0] * self._n
        if not self._n or not self._avgdl:
            return scores
        for term in set(_tokenize(query)):
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (self._n - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self._term_freqs):
                freq = tf.get(term, 0)
                if not freq:
                    continue
                denom = freq + self._k1 * (
                    1 - self._b + self._b * self._lengths[i] / self._avgdl
                )
                scores[i] += idf * freq * (self._k1 + 1) / denom
        return scores


class CodebaseRetriever:
    def __init__(self, collection_name: str = COLLECTION_NAME) -> None:
        self.client = get_chroma_client()
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=get_embedding_function(),
        )
        self._corpus: list[str] | None = None
        self._metadatas: list[Metadata] = []
        self._bm25: _BM25 | None = None

    def add_chunks(
        self, chunks: list[str], metadatas: list[Metadata], ids: list[str]
    ) -> None:
        self.collection.add(documents=chunks, metadatas=metadatas, ids=ids)
        self._corpus = None  # invalidate the lexical index
        self._bm25 = None

    def _ensure_corpus(self) -> None:
        if self._corpus is not None:
            return
        got = self.collection.get(include=["documents", "metadatas"])
        self._corpus = list(got.get("documents") or [])
        self._metadatas = list(got.get("metadatas") or [])
        self._bm25 = _BM25(self._corpus) if self._corpus else None

    def search(self, query: str, top_k: int = 5) -> list[str]:
        total = self.collection.count()
        if total == 0:
            return []
        self._ensure_corpus()
        corpus = self._corpus or []

        # Vector candidates.
        n = min(max(top_k * 4, top_k), total)
        res = self.collection.query(
            query_texts=[query], n_results=n, include=["documents", "metadatas"]
        )
        vector_docs = (res.get("documents") or [[]])[0]
        vector_metas = (res.get("metadatas") or [[]])[0]

        # Lexical candidates (BM25), mapped back to their corpus index.
        lexical: list[tuple[int, str, Metadata]] = []
        if self._bm25 is not None:
            scored = sorted(
                enumerate(self._bm25.scores(query)), key=lambda p: p[1], reverse=True
            )[:n]
            for idx, score in scored:
                if score <= 0:
                    break
                meta = self._metadatas[idx] if idx < len(self._metadatas) else {}
                lexical.append((idx, corpus[idx], meta))

        # Reciprocal rank fusion of the two rankings.
        fused: dict[str, float] = {}
        payload: dict[str, tuple[str, Metadata]] = {}

        def add(rank: int, doc: str, meta: Metadata, seen: set[str]) -> None:
            file_path = (meta or {}).get("file_path") or doc[:80]
            key = str(file_path)
            # One contribution per file per ranking. Summing every chunk a file
            # matched let a file with five mediocre chunks outrank the file holding
            # the single best match.
            if key in seen:
                return
            seen.add(key)
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
            payload.setdefault(key, (doc, meta))

        seen_vector: set[str] = set()
        for rank, (doc, meta) in enumerate(zip(vector_docs, vector_metas)):
            add(rank, doc, meta, seen_vector)
        seen_lexical: set[str] = set()
        for rank, (_idx, doc, meta) in enumerate(lexical):
            add(rank, doc, meta, seen_lexical)

        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        picked: list[str] = []
        seen_files: set[str] = set()
        for key, _score in ordered:
            if key in seen_files:
                continue
            seen_files.add(key)
            picked.append(payload[key][0])
            if len(picked) >= top_k:
                break
        return picked


_retrievers: dict[str, CodebaseRetriever] = {}
_lock = threading.Lock()


def reset_collection(collection_name: str) -> None:
    # Re-index idempotency: drop the stale collection + cached handle so a
    # re-analyze of the same path doesn't duplicate every chunk.
    with _lock:
        try:
            get_chroma_client().delete_collection(collection_name)
        except Exception:  # ponytail: chroma raises if it never existed
            pass
        _retrievers.pop(collection_name, None)


def get_retriever(collection_name: str | None = None) -> CodebaseRetriever:
    # ponytail: one client + one model per process, guarded so the first
    # concurrent uploads can't each load Jina / open a second PersistentClient
    # (concurrent model loads deadlock on meta tensors). No name → active project.
    name = collection_name or COLLECTION_NAME
    if name not in _retrievers:
        with _lock:
            if name not in _retrievers:
                _retrievers[name] = CodebaseRetriever(name)
    return _retrievers[name]
