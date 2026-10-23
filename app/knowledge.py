"""Knowledge placeholder.

This is NOT retrieval or RAG yet. It always returns the whole sample file.
Embeddings and vector search come in a later step.
"""

from pathlib import Path

SAMPLE_FILE = Path(__file__).resolve().parent.parent / "knowledge" / "sample.md"


def get_instructions(message: str) -> str:
    return SAMPLE_FILE.read_text(encoding="utf-8")
