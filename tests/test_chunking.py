"""Chunking tests. Uses the real model tokenizer for the size limit."""

import pytest

from app import knowledge
from app.chunking import Chunk, chunk_document, chunk_folder
from app.embeddings import get_embedder
from app.knowledge import ACTIVE_DOCUMENTS, KNOWLEDGE_DIR
from tests.conftest import FIXTURES_DIR, SAMPLE_DOCUMENTS


def word_count(text: str) -> int:
    return len(text.split())


def test_limit_is_read_from_the_model():
    embedder = get_embedder()
    assert embedder.max_tokens == embedder.model.max_seq_length
    # Special tokens are counted: an empty string is still <s> + </s>.
    assert embedder.count_tokens("") == 2


def test_only_the_two_user_guides_are_indexed():
    """The app searches exactly the reviewed English and Hebrew guides - nothing else in knowledge/."""
    assert ACTIVE_DOCUMENTS == ("about_english.md", "about_hebrew.md")
    indexed = {c.filename for c in knowledge.get_store().chunks}
    assert indexed == set(ACTIVE_DOCUMENTS)


def test_unlisted_files_are_skipped(tmp_path, caplog):
    (tmp_path / "a_guide.md").write_text("# Guide\nline one", encoding="utf-8")
    (tmp_path / "b_other.md").write_text("# Other\nunrelated content", encoding="utf-8")
    (tmp_path / "c_notes.txt").write_text("not markdown", encoding="utf-8")
    with caplog.at_level("WARNING"):
        chunks = chunk_folder(tmp_path, word_count, 50, filenames=("a_guide.md",))
    assert {c.filename for c in chunks} == {"a_guide.md"}
    assert "Not indexed: b_other.md" in caplog.text
    assert "Not indexed: c_notes.txt" in caplog.text
    # Without a list every .md file would be read.
    assert {c.filename for c in chunk_folder(tmp_path, word_count, 50)} == {"a_guide.md", "b_other.md"}


def test_missing_listed_document_raises(tmp_path):
    (tmp_path / "a_guide.md").write_text("# Guide\nline one", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="b_guide.md"):
        chunk_folder(tmp_path, word_count, 50, filenames=("a_guide.md", "b_guide.md"))


def test_sample_fixture_chunks_keep_filename_heading_and_fit_the_model():
    embedder = get_embedder()
    chunks = chunk_folder(FIXTURES_DIR, embedder.count_tokens, embedder.max_tokens, filenames=SAMPLE_DOCUMENTS)
    assert {c.filename for c in chunks} == set(SAMPLE_DOCUMENTS)
    assert len(chunks) == 11
    assert Chunk("visits.md", "Cancelling a visit", "") in [
        Chunk(c.filename, c.heading, "") for c in chunks
    ]
    for chunk in chunks:
        assert embedder.count_tokens(chunk.embed_text) <= embedder.max_tokens


def test_user_guide_chunks_fit_the_model():
    embedder = get_embedder()
    chunks = chunk_folder(KNOWLEDGE_DIR, embedder.count_tokens, embedder.max_tokens, filenames=ACTIVE_DOCUMENTS)
    assert {c.filename for c in chunks} == set(ACTIVE_DOCUMENTS)
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
