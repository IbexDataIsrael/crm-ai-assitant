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


# --- Document and section context (instructions7) ---------------------------

GUIDE_CONTEXT = [
    # (file, document title, first section, start of that section's explanation)
    ("about_english.md", "Jewish Points CRM - User Guide", "1. What this system is", "A bilingual (Hebrew/English) CRM"),
    ("about_hebrew.md", "נקודות יהודיות CRM - מדריך למשתמש", "1. מהי המערכת הזו?", "מערכת CRM דו-לשונית"),
]


def guide_chunks():
    embedder = get_embedder()
    return chunk_folder(KNOWLEDGE_DIR, embedder.count_tokens, embedder.max_tokens, filenames=ACTIVE_DOCUMENTS)


@pytest.mark.parametrize("filename, title, section, explanation", GUIDE_CONTEXT)
def test_intro_explanation_keeps_product_and_section_context(filename, title, section, explanation):
    """The chunk that explains the system carries the guide title and the section title it belongs to."""
    chunk = next(c for c in guide_chunks() if c.filename == filename and c.text.startswith(explanation))
    assert chunk.title == title
    assert chunk.heading == section
    assert title in chunk.embed_text and section in chunk.embed_text


@pytest.mark.parametrize("filename, title, section, explanation", GUIDE_CONTEXT)
def test_every_guide_chunk_has_body_text_and_context(filename, title, section, explanation):
    """No chunk is just a title or section heading; every chunk knows its guide and section."""
    chunks = [c for c in guide_chunks() if c.filename == filename]
    numbered_sections = {c.heading for c in chunks}
    assert section in numbered_sections and len(numbered_sections) == 7
    for chunk in chunks:
        assert chunk.title == title
        lines = chunk.text.splitlines()
        assert lines and not all(line == title or line in numbered_sections for line in lines), chunk


def test_numbered_lines_are_steps_when_the_document_has_markdown_headings():
    """Fixtures use '#' headings and numbered steps: the steps stay body text."""
    doc = "# Visits\n## Before a visit\n1. Confirm the time\n2. Tell the coordinator"
    chunks = chunk_document("v.md", doc, word_count, max_tokens=50)
    assert [(c.title, c.heading, c.text) for c in chunks] == [
        ("Visits", "Before a visit", "1. Confirm the time\n2. Tell the coordinator")
    ]


def test_plain_title_and_numbered_sections_without_markdown_headings():
    doc = "My Guide\n\n1. What it is\nA tool for things.\n2. Signing in\nUse your password."
    chunks = chunk_document("g.md", doc, word_count, max_tokens=50)
    assert [(c.title, c.heading, c.text) for c in chunks] == [
        ("My Guide", "1. What it is", "A tool for things."),
        ("My Guide", "2. Signing in", "Use your password."),
    ]
    assert chunks[0].embed_text == "My Guide > 1. What it is\nA tool for things."
