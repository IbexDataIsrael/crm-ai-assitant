"""Knowledge route: local semantic retrieval over knowledge/, plus the reply text.

The guide chunks and their vectors live in PostgreSQL + pgvector (app/vectorstore.py);
startup syncs the table with the guides, re-embedding only what changed.

Retrieval returns the three most relevant passages with scores, filenames and
headings. If answer generation (Gemini or Claude) succeeded, its cited answer is shown above the
passages; otherwise the reply says why there is no answer and shows the
passages only.
"""

from functools import lru_cache
from pathlib import Path

from app.embeddings import get_embedder
from app.generation import GenerationResult
from app.vectorstore import VectorStore

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
RETRIEVAL_LABEL = "Local semantic retrieval - source passages"
# The reviewed user guides (English and Hebrew) are the only searchable
# documents. Startup fails if one is missing; any other file in knowledge/ is
# skipped and logged.
ACTIVE_DOCUMENTS = ("about_english.md", "about_hebrew.md")
TOP_K = 3


@lru_cache(maxsize=1)
def get_store() -> VectorStore:
    """Sync the guides into the database once per process (called at app startup)."""
    store = VectorStore(get_embedder())
    store.build(KNOWLEDGE_DIR, filenames=ACTIVE_DOCUMENTS)
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


def answer_header(generation: GenerationResult) -> list[str]:
    """The top of the reply: the answer, or a plain statement of why there is none."""
    if generation.status == "answered":
        cited = "; ".join(f"[{c['number']}] {c['filename']} > {c['heading']}" for c in generation.citations)
        return [
            f"AI-generated answer ({generation.provider}, {generation.model}) - based only on the passages below:",
            generation.answer,
            f"Cited: {cited}",
        ]
    if generation.status == "insufficient_sources":
        return [f"No answer: {generation.provider} ({generation.model}) found that the passages below don't answer this question."]
    if generation.status == "no_api_key":
        return [f"No AI-written answer: answer generation is off because {generation.error}."]
    return [f"No AI-written answer: answer generation failed ({generation.error})."]


def format_reply(message: str, passages: list[dict], generation: GenerationResult) -> str:
    lines = answer_header(generation) + ["", RETRIEVAL_LABEL + ":", ""]
    embedder = get_embedder()
    if embedder.count_tokens(message) > embedder.max_tokens:
        lines += [f"Note: your question is longer than {embedder.max_tokens} tokens; only the start was used for search.", ""]
    for i, p in enumerate(passages, 1):
        lines += [f"[{i}] {p['filename']} - {p['heading']} (similarity {p['score']:.2f})", p["text"], ""]
    lines.append("Sources: " + "; ".join(f"{p['filename']} > {p['heading']}" for p in passages))
    return "\n".join(lines)
