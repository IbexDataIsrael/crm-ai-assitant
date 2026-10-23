"""Knowledge route: local semantic retrieval over knowledge/, plus the reply text.

Retrieval returns the three most relevant passages with scores, filenames and
headings. If Gemini generation succeeded, its cited answer is shown above the
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


def answer_header(generation: GenerationResult) -> list[str]:
    """The top of the reply: the answer, or a plain statement of why there is none."""
    if generation.status == "answered":
        cited = "; ".join(f"[{c['number']}] {c['filename']} > {c['heading']}" for c in generation.citations)
        return [
            f"AI-generated answer (Gemini, {generation.model}) - based only on the passages below:",
            generation.answer,
            f"Cited: {cited}",
        ]
    if generation.status == "insufficient_sources":
        return [f"No answer: Gemini ({generation.model}) found that the passages below don't answer this question."]
    if generation.status == "no_api_key":
        return ["No AI-written answer: answer generation is off because GEMINI_API_KEY is not set."]
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
