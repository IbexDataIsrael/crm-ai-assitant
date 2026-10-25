"""Turn text into vectors with a real pretrained model, running locally on CPU.

Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2. It is
downloaded into the image at build time; at runtime it loads from that local
cache (HF_HUB_OFFLINE=1), so no text leaves the container.

The interface is deliberately small - max_tokens, count_tokens, encode - so a
different provider (for example a hosted embedding API) could replace this
class later without changing chunking or the vector store.
"""

import logging
import os
import time
from functools import lru_cache
from typing import Protocol

import numpy as np

MODEL_NAME = os.environ.get("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
MODEL_REVISION = os.environ.get("EMBED_REVISION", "e8f8c211226b894fcb81acc59f3b34ba3efd5f42")

log = logging.getLogger("uvicorn.error")


class Embedder(Protocol):
    max_tokens: int
    model_id: str  # model + revision; part of each stored chunk's content hash

    def count_tokens(self, text: str) -> int: ...

    def encode(self, texts: list[str]) -> np.ndarray: ...


class LocalSentenceTransformer:
    def __init__(self, model_name: str = MODEL_NAME, revision: str = MODEL_REVISION):
        from sentence_transformers import SentenceTransformer

        started = time.perf_counter()
        self.model = SentenceTransformer(model_name, revision=revision, device="cpu")
        self.load_seconds = time.perf_counter() - started
        self.model_id = f"{model_name}@{revision}"
        self.tokenizer = self.model.tokenizer
        # The model reads at most max_seq_length tokens and silently drops the
        # rest, so that (not the tokenizer's larger default) is the real limit.
        self.max_tokens = min(self.model.max_seq_length, self.tokenizer.model_max_length)
        log.info(
            "Embedding model loaded in %.1fs: %s (max %d tokens incl. special tokens)",
            self.load_seconds, model_name, self.max_tokens,
        )

    def count_tokens(self, text: str) -> int:
        """Token count as the model sees it, including special tokens like <s> and </s>."""
        return len(self.tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"])

    def encode(self, texts: list[str]) -> np.ndarray:
        """One vector per text, shape (len(texts), 384)."""
        return self.model.encode(texts, convert_to_numpy=True, show_progress_bar=False)


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Load the model once per process and reuse it."""
    return LocalSentenceTransformer()
