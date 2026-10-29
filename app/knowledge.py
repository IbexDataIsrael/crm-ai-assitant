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


# The assistant's own wording, by the user's language. Passage text, filenames and
# headings are guide content and are shown as they are; a provider's technical
# error detail ({error}) stays as the provider wrote it.
TEXTS = {
    "en": {
        "answered": "AI-generated answer ({provider}, {model}) - based only on the passages below:",
        "cited": "Cited: {cited}",
        "insufficient": "No answer: {provider} ({model}) found that the passages below don't answer this question.",
        "no_api_key": "No AI-written answer: answer generation is off because {error}.",
        "failed": "No AI-written answer: answer generation failed ({error}).",
        "retrieval": "Local semantic retrieval - source passages:",
        "too_long": "Note: your question is longer than {max_tokens} tokens; only the start was used for search.",
        "passage": "[{number}] {filename} - {heading} (similarity {score:.2f})",
        "sources": "Sources: {sources}",
    },
    "he": {
        "answered": "תשובה שנכתבה ב-AI ({provider}, {model}) - מבוססת רק על הקטעים שלהלן:",
        "cited": "צוטטו: {cited}",
        "insufficient": "אין תשובה: {provider} ({model}) מצא שהקטעים שלהלן אינם עונים על השאלה.",
        "no_api_key": "אין תשובה שנכתבה ב-AI: יצירת התשובות כבויה ({error}).",
        "failed": "אין תשובה שנכתבה ב-AI: יצירת התשובה נכשלה ({error}).",
        "retrieval": "חיפוש סמנטי מקומי - קטעי מקור:",
        "too_long": "שימו לב: השאלה ארוכה מ-{max_tokens} טוקנים; רק תחילתה שימשה לחיפוש.",
        "passage": "[{number}] {filename} - {heading} (דמיון {score:.2f})",
        "sources": "מקורות: {sources}",
    },
}


def answer_header(generation: GenerationResult, language: str = "en") -> list[str]:
    """The top of the reply: the answer, or a plain statement of why there is none."""
    texts = TEXTS[language]
    if generation.status == "answered":
        cited = "; ".join(f"[{c['number']}] {c['filename']} > {c['heading']}" for c in generation.citations)
        return [
            texts["answered"].format(provider=generation.provider, model=generation.model),
            generation.answer,
            texts["cited"].format(cited=cited),
        ]
    if generation.status == "insufficient_sources":
        return [texts["insufficient"].format(provider=generation.provider, model=generation.model)]
    if generation.status == "no_api_key":
        return [texts["no_api_key"].format(error=generation.error)]
    return [texts["failed"].format(error=generation.error)]


def format_reply(message: str, passages: list[dict], generation: GenerationResult, language: str = "en") -> str:
    texts = TEXTS[language]
    lines = answer_header(generation, language) + ["", texts["retrieval"], ""]
    embedder = get_embedder()
    if embedder.count_tokens(message) > embedder.max_tokens:
        lines += [texts["too_long"].format(max_tokens=embedder.max_tokens), ""]
    for i, p in enumerate(passages, 1):
        lines += [texts["passage"].format(number=i, filename=p["filename"], heading=p["heading"], score=p["score"]),
                  p["text"], ""]
    lines.append(texts["sources"].format(sources="; ".join(f"{p['filename']} > {p['heading']}" for p in passages)))
    return "\n".join(lines)
