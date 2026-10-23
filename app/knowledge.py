"""Knowledge route: local semantic retrieval over knowledge/.

Returns the three most relevant passages with scores, filenames and headings.
There is no AI-written answer yet - the reply shows the source passages only.
Generating an answer from these passages is the next stage.
"""

from functools import lru_cache
from pathlib import Path

from app.embeddings import get_embedder
from app.vectorstore import VectorStore

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
RETRIEVAL_LABEL = "Local semantic retrieval - source passages, no AI-written answer yet"
TOP_K = 3


@lru_cache(maxsize=1)
def get_store() -> VectorStore:
    """Build the in-memory store once per process (called at app startup)."""
    store = VectorStore(get_embedder())
    store.build(KNOWLEDGE_DIR)
    return store


def search(message: str, k: int = TOP_K) -> list[dict]:
    return [
        {
            "score": round(r.score, 3),
            "filename": r.chunk.filename,
            "heading": r.chunk.heading,
            "text": r.chunk.text,
        }
        for r in get_store().search(message, k)
    ]


def format_reply(message: str, passages: list[dict]) -> str:
    lines = [RETRIEVAL_LABEL, ""]
    embedder = get_embedder()
    if embedder.count_tokens(message) > embedder.max_tokens:
        lines += [f"Note: your question is longer than {embedder.max_tokens} tokens; only the start was used.", ""]
    for i, p in enumerate(passages, 1):
        lines += [f"{i}. {p['filename']} - {p['heading']} (similarity {p['score']:.2f})", p["text"], ""]
    lines.append("Sources: " + "; ".join(f"{p['filename']} > {p['heading']}" for p in passages))
    return "\n".join(lines)
