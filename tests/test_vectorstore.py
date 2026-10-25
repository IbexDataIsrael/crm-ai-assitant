"""The Postgres + pgvector store: real rows, SQL cosine search, and incremental sync.

Runs against the test database (see conftest.py) with the real local model.
"""

import numpy as np
import psycopg
import pytest

from app import knowledge
from app.embeddings import get_embedder
from app.vectorstore import VectorStore, content_hash


class CountingEmbedder:
    """The real embedder, but it remembers every text it was asked to embed."""

    def __init__(self):
        self.real = get_embedder()
        self.max_tokens = self.real.max_tokens
        self.model_id = self.real.model_id
        self.count_tokens = self.real.count_tokens
        self.encoded: list[str] = []

    def encode(self, texts):
        self.encoded.extend(texts)
        return self.real.encode(texts)


def rows(store: VectorStore) -> list[tuple]:
    with store.connect() as conn:
        return conn.execute(
            "SELECT filename, position, heading, text, content_hash FROM chunks"
            " WHERE collection = %s ORDER BY filename, position",
            (store.collection,),
        ).fetchall()


@pytest.fixture
def sync_store(test_database):
    """An empty collection of its own, emptied again afterwards."""
    store = VectorStore(CountingEmbedder(), collection="test_sync")
    with store.connect() as conn:
        conn.execute("DELETE FROM chunks WHERE collection = %s", (store.collection,))
    yield store
    with store.connect() as conn:
        conn.execute("DELETE FROM chunks WHERE collection = %s", (store.collection,))


def write_docs(folder, docs: dict[str, str]):
    for path in folder.glob("*.md"):
        path.unlink()
    for name, text in docs.items():
        (folder / name).write_text(text, encoding="utf-8")


DOC_A = "# Guide A\n## Signing in\nUse your email and password.\n## Donations\nCount cash with a second volunteer."
DOC_B = "# Guide B\n## Visits\nCall the family before you go."


def test_first_build_embeds_and_stores_every_chunk(sync_store, tmp_path):
    write_docs(tmp_path, {"a.md": DOC_A, "b.md": DOC_B})
    report = sync_store.build(tmp_path)
    assert (report.kept, report.reused, report.embedded, report.removed) == (0, 0, 3, 0)
    stored = rows(sync_store)
    assert [(f, p, h) for f, p, h, _, _ in stored] == [
        ("a.md", 0, "Signing in"), ("a.md", 1, "Donations"), ("b.md", 0, "Visits"),
    ]
    model_id = sync_store.embedder.model_id
    assert [digest for *_, digest in stored] == [content_hash(model_id, c.embed_text) for c in sync_store.chunks]


def test_unchanged_documents_are_not_embedded_again(sync_store, tmp_path):
    write_docs(tmp_path, {"a.md": DOC_A, "b.md": DOC_B})
    sync_store.build(tmp_path)
    sync_store.embedder.encoded.clear()

    report = sync_store.build(tmp_path)

    assert (report.kept, report.reused, report.embedded, report.removed) == (3, 0, 0, 0)
    assert sync_store.embedder.encoded == []


def test_changed_chunk_is_reembedded_and_obsolete_rows_removed(sync_store, tmp_path):
    write_docs(tmp_path, {"a.md": DOC_A, "b.md": DOC_B})
    sync_store.build(tmp_path)
    sync_store.embedder.encoded.clear()

    # Change one chunk of a.md and drop b.md entirely.
    write_docs(tmp_path, {"a.md": DOC_A.replace("second volunteer", "colleague")})
    report = sync_store.build(tmp_path)

    assert (report.kept, report.reused, report.embedded, report.removed) == (1, 0, 1, 1)
    assert len(sync_store.embedder.encoded) == 1 and "colleague" in sync_store.embedder.encoded[0]
    assert [(f, t) for f, _, _, t, _ in rows(sync_store)] == [
        ("a.md", "Use your email and password."),
        ("a.md", "Count cash with a colleague."),
    ]


def test_moved_chunk_reuses_its_stored_vector(sync_store, tmp_path):
    write_docs(tmp_path, {"a.md": DOC_A})
    sync_store.build(tmp_path)
    sync_store.embedder.encoded.clear()

    # A new first section shifts the existing chunks one position down.
    write_docs(tmp_path, {"a.md": DOC_A.replace("# Guide A\n", "# Guide A\n## Welcome\nHello volunteers.\n")})
    report = sync_store.build(tmp_path)

    assert (report.kept, report.reused, report.embedded, report.removed) == (0, 2, 1, 0)
    assert len(sync_store.embedder.encoded) == 1 and "Hello volunteers." in sync_store.embedder.encoded[0]
    assert [h for _, _, h, _, _ in rows(sync_store)] == ["Welcome", "Signing in", "Donations"]


def test_guide_rows_hold_384_number_vectors_and_their_context():
    store = knowledge.get_store()
    with store.connect() as conn:
        stored = conn.execute(
            "SELECT filename, title, heading, text, embed_text, vector_dims(embedding) FROM chunks"
            " WHERE collection = %s ORDER BY filename, position",
            (store.collection,),
        ).fetchall()
    assert len(stored) == len(store.chunks) > 0
    for (filename, title, heading, text, embed_text, dims), chunk in zip(stored, store.chunks):
        assert (filename, title, heading, text, embed_text) == (
            chunk.filename, chunk.title, chunk.heading, chunk.text, chunk.embed_text,
        )
        assert dims == 384


def test_sql_score_matches_cosine_similarity_of_the_stored_vectors():
    store = knowledge.get_store()
    question = "how do I reset my password"
    top = store.search(question, k=3)
    question_vector = get_embedder().encode([question])[0]
    with store.connect() as conn:
        for result in top:
            (vector_text,) = conn.execute(
                "SELECT embedding::text FROM chunks WHERE collection = %s AND embed_text = %s",
                (store.collection, result.chunk.embed_text),
            ).fetchone()
            vector = np.array([float(x) for x in vector_text.strip("[]").split(",")])
            cosine = np.dot(question_vector, vector) / (np.linalg.norm(question_vector) * np.linalg.norm(vector))
            assert result.score == pytest.approx(cosine, abs=1e-5)
    assert [r.score for r in top] == sorted((r.score for r in top), reverse=True)


def test_tests_never_use_the_app_database(test_database):
    assert knowledge.get_store().dsn == test_database
    assert psycopg.conninfo.conninfo_to_dict(test_database)["dbname"] == "crm_assistant_test"


def test_tests_log_in_as_the_restricted_test_role(test_database):
    with psycopg.connect(test_database) as conn:
        user, superuser, createdb = conn.execute(
            "SELECT current_user, rolsuper, rolcreatedb FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    assert (user, superuser, createdb) == ("crm_test", False, False)


def test_test_role_cannot_open_the_app_database(test_database):
    app_database = psycopg.conninfo.make_conninfo(test_database, dbname="crm_assistant")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(app_database, connect_timeout=10)


def test_test_role_cannot_change_the_schema(test_database):
    with psycopg.connect(test_database) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE not_allowed (id integer)")
