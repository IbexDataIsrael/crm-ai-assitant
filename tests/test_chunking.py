"""Chunking tests. Uses the real model tokenizer for the size limit."""

import pytest

from app.chunking import Chunk, chunk_document, chunk_folder
from app.embeddings import get_embedder
from app.knowledge import KNOWLEDGE_DIR


def word_count(text: str) -> int:
    return len(text.split())


def test_limit_is_read_from_the_model():
    embedder = get_embedder()
    assert embedder.max_tokens == embedder.model.max_seq_length
    # Special tokens are counted: an empty string is still <s> + </s>.
    assert embedder.count_tokens("") == 2


def test_every_knowledge_file_is_marked_sample_data():
    for path in KNOWLEDGE_DIR.glob("*.md"):
        assert "SAMPLE DATA - fictional" in path.read_text(encoding="utf-8"), path.name


def test_knowledge_chunks_keep_filename_heading_and_fit_the_model():
    embedder = get_embedder()
    chunks = chunk_folder(KNOWLEDGE_DIR, embedder.count_tokens, embedder.max_tokens)
    assert {c.filename for c in chunks} == {"sample.md", "visits.md", "donations.md"}
    assert Chunk("visits.md", "Cancelling a visit", "") in [
        Chunk(c.filename, c.heading, "") for c in chunks
    ]
    for chunk in chunks:
        assert embedder.count_tokens(chunk.embed_text) <= embedder.max_tokens


def test_long_line_is_split_not_truncated():
    embedder = get_embedder()
    words = [f"word{i}" for i in range(400)]
    chunks = chunk_document("long.md", "# Long\n" + " ".join(words), embedder.count_tokens, embedder.max_tokens)
    assert len(chunks) > 1
    for chunk in chunks:
        assert embedder.count_tokens(chunk.embed_text) <= embedder.max_tokens
    # Every word survives somewhere, in order.
    seen = [w for c in chunks for w in c.text.split()]
    assert sorted(set(seen), key=seen.index) == words


def test_neighbouring_chunks_overlap():
    doc = "# H\nline one a\nline two b\nline three c\nline four d"
    chunks = chunk_document("x.md", doc, word_count, max_tokens=7)
    assert len(chunks) > 1
    for before, after in zip(chunks, chunks[1:]):
        assert before.text.splitlines()[-1] == after.text.splitlines()[0]


def test_unsplittable_text_raises_instead_of_truncating():
    with pytest.raises(ValueError):
        chunk_document("x.md", "# H\nabc", lambda text: 99, max_tokens=10)
