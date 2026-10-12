from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.types import EmbeddingFunction, Embeddings
from sentence_transformers import SentenceTransformer

MODEL_NAME = "jinaai/jina-embeddings-v2-base-code"
# Pins the weights so a re-download cannot silently change embeddings under an
# existing index. It does NOT pin the remote modelling code — see the note in
# JinaCodeEmbeddingFunction.__init__.
MODEL_REVISION = "516f4baf13dec4ddddda8631e019b5737c8bc250"
# Anchored to the package, not the working directory: "./chroma_data" silently
# created a second, empty store whenever uvicorn started from anywhere but backend/.
CHROMA_PATH = Path(__file__).resolve().parents[1] / "chroma_data"


class JinaCodeEmbeddingFunction(EmbeddingFunction[Any]):
    def __init__(self) -> None:
        # trust_remote_code is required here: this model's config auto_map points at
        # custom modelling code that HuggingFace fetches and executes at load time.
        # That code lives in a *different* repo (jinaai/jina-bert-v2-qk-post-norm),
        # so MODEL_REVISION does not pin it.
        # shortcut: an unreviewed code fetch at load. To remove it, vendor those .py
        # files into the repo and load from the local path, or ship with a warm cache
        # and HF_HUB_OFFLINE=1 so nothing is fetched at run time.
        self.model = SentenceTransformer(
            MODEL_NAME, trust_remote_code=True, revision=MODEL_REVISION
        )

    def __call__(self, input: Any) -> Embeddings:
        return self.model.encode(list(input), convert_to_numpy=True).tolist()

    @staticmethod
    def name() -> str:
        return "jina-code"

    def get_config(self) -> dict[str, Any]:
        return {"model_name": MODEL_NAME}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "JinaCodeEmbeddingFunction":
        return get_embedding_function()


_embedding_function: JinaCodeEmbeddingFunction | None = None


def get_embedding_function() -> JinaCodeEmbeddingFunction:
    # ponytail: single model per process. Chroma rebuilds the EF from config
    # when opening a persisted collection; constructing a fresh SentenceTransformer
    # there loads Jina again and can deadlock on meta tensors.
    global _embedding_function
    if _embedding_function is None:
        _embedding_function = JinaCodeEmbeddingFunction()
    return _embedding_function


def get_chroma_client() -> ClientAPI:
    return chromadb.PersistentClient(path=str(CHROMA_PATH))
