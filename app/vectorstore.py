"""A tiny in-memory vector store.

At startup every chunk from knowledge/ is turned into a vector and kept in a
Python list. A search embeds the question and compares it with every chunk
vector using cosine similarity. This is real vector search, just over a small
corpus and without a database: nothing is saved, it is rebuilt on each start.
(A persistent store such as Postgres + pgvector is a later decision.)
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.chunking import Chunk, chunk_folder
from app.embeddings import Embedder


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """How closely two vectors point the same way: 1.0 = same direction, 0 = unrelated.

    cos(angle) = (a . b) / (|a| * |b|)
    """
    dot = float(np.dot(a, b))
    length_a = float(np.linalg.norm(a))
    length_b = float(np.linalg.norm(b))
    if length_a == 0 or length_b == 0:
        return 0.0
    return dot / (length_a * length_b)


@dataclass(frozen=True)
class SearchResult:
    score: float
    chunk: Chunk


class VectorStore:
    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.chunks: list[Chunk] = []
        self.vectors: list[np.ndarray] = []

    def build(self, folder: Path) -> None:
        """Chunk and embed every document in the folder (replaces anything stored)."""
        self.chunks = chunk_folder(folder, self.embedder.count_tokens, self.embedder.max_tokens)
        self.vectors = list(self.embedder.encode([c.embed_text for c in self.chunks]))

    def search(self, question: str, k: int = 3) -> list[SearchResult]:
        """Return the k chunks most similar to the question, best first."""
        question_vector = self.embedder.encode([question])[0]
        results = [
            SearchResult(cosine_similarity(question_vector, vector), chunk)
            for chunk, vector in zip(self.chunks, self.vectors)
        ]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:k]
