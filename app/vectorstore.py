"""Vector store in PostgreSQL + pgvector.

Every chunk is a row in the `chunks` table: filename, position in its
document, title, heading, text, the exact text that was embedded, a content
hash and the 384-number vector (pgvector's `vector(384)` type). A search
embeds the question and asks Postgres for the nearest rows by cosine distance
(`<=>`); similarity = 1 - distance, the same number the old in-memory store
computed.

Indexing (`build`) syncs the table with the documents instead of starting over:
- a chunk whose content hash is already stored keeps (or reuses) its vector,
  so an unchanged guide is never embedded again;
- a new or changed chunk is embedded and written;
- a row whose chunk no longer exists is deleted.
The hash covers the embedding model + revision and the embedded text, so
switching models re-embeds everything.

Rows are grouped by `collection` ("guides" for the app, a separate one for
test fixtures). Search is an exact scan (no approximate index): for a few
dozen rows that is fast and gives exactly the same ranking as before.
"""

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import psycopg

from app.chunking import Chunk, chunk_folder
from app.embeddings import Embedder

log = logging.getLogger("uvicorn.error")

# The db service in docker-compose.yml. Reachable only on the compose network.
DEFAULT_DATABASE_URL = "postgresql://crm_assistant@db:5432/crm_assistant"
EMBEDDING_DIMENSIONS = 384

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    collection   text    NOT NULL,
    filename     text    NOT NULL,
    position     integer NOT NULL,  -- order of the chunk within its document
    title        text    NOT NULL,
    heading      text    NOT NULL,
    text         text    NOT NULL,
    embed_text   text    NOT NULL,  -- "title > heading" + text: what was embedded
    content_hash text    NOT NULL,  -- sha256 of model id + embed_text
    embedding    vector({EMBEDDING_DIMENSIONS}) NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection, filename, position)
);
"""


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def content_hash(model_id: str, embed_text: str) -> str:
    return hashlib.sha256(f"{model_id}\n{embed_text}".encode("utf-8")).hexdigest()


def to_pgvector(vector: np.ndarray) -> str:
    """pgvector's text format: [0.1,0.2,...]."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


@dataclass(frozen=True)
class SearchResult:
    score: float
    chunk: Chunk


@dataclass(frozen=True)
class SyncReport:
    kept: int  # unchanged rows, vector untouched
    reused: int  # chunk moved to another position; stored vector copied, not re-embedded
    embedded: int  # new or changed chunks, embedded now
    removed: int  # rows whose chunk no longer exists


class VectorStore:
    def __init__(self, embedder: Embedder, dsn: str | None = None, collection: str = "guides"):
        self.embedder = embedder
        self.dsn = dsn or database_url()
        self.collection = collection
        # The chunks from the last build, in document order.
        self.chunks: list[Chunk] = []

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, connect_timeout=10)

    def build(self, folder: Path, filenames: tuple[str, ...] | None = None) -> SyncReport:
        """Chunk the folder's documents and sync the collection's rows with them."""
        self.chunks = chunk_folder(folder, self.embedder.count_tokens, self.embedder.max_tokens, filenames)
        wanted = {}  # (filename, position) -> (chunk, hash)
        positions: dict[str, int] = {}
        for chunk in self.chunks:
            position = positions.get(chunk.filename, 0)
            positions[chunk.filename] = position + 1
            wanted[(chunk.filename, position)] = (chunk, content_hash(self.embedder.model_id, chunk.embed_text))

        with self.connect() as conn:
            conn.execute(SCHEMA)
            # One indexer at a time per collection (e.g. app startup and a test run).
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (self.collection,))
            stored_rows = conn.execute(
                "SELECT filename, position, content_hash, embedding::text FROM chunks WHERE collection = %s",
                (self.collection,),
            ).fetchall()
            stored_hash = {(f, p): h for f, p, h, _ in stored_rows}
            vector_by_hash = {h: v for _, _, h, v in stored_rows}

            kept, to_copy, to_embed = 0, [], []
            for key, (chunk, digest) in wanted.items():
                if stored_hash.get(key) == digest:
                    kept += 1
                elif digest in vector_by_hash:
                    to_copy.append((key, chunk, digest, vector_by_hash[digest]))
                else:
                    to_embed.append((key, chunk, digest))

            if to_embed:
                vectors = self.embedder.encode([chunk.embed_text for _, chunk, _ in to_embed])
                to_copy += [(key, chunk, digest, to_pgvector(v)) for (key, chunk, digest), v in zip(to_embed, vectors)]

            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO chunks (collection, filename, position, title, heading, text,
                                        embed_text, content_hash, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (collection, filename, position) DO UPDATE SET
                        title = EXCLUDED.title, heading = EXCLUDED.heading, text = EXCLUDED.text,
                        embed_text = EXCLUDED.embed_text, content_hash = EXCLUDED.content_hash,
                        embedding = EXCLUDED.embedding, updated_at = now()
                    """,
                    [
                        (self.collection, f, p, c.title, c.heading, c.text, c.embed_text, digest, vector)
                        for (f, p), c, digest, vector in to_copy
                    ],
                )
                obsolete = [key for key in stored_hash if key not in wanted]
                cur.executemany(
                    "DELETE FROM chunks WHERE collection = %s AND filename = %s AND position = %s",
                    [(self.collection, f, p) for f, p in obsolete],
                )

        report = SyncReport(kept, len(to_copy) - len(to_embed), len(to_embed), len(obsolete))
        log.info(
            "Vector store '%s': %d chunks - %d unchanged, %d moved (vector reused), %d embedded, %d removed",
            self.collection, len(self.chunks), report.kept, report.reused, report.embedded, report.removed,
        )
        return report

    def search(self, question: str, k: int = 3) -> list[SearchResult]:
        """Return the k chunks most similar to the question, best first."""
        question_vector = to_pgvector(self.embedder.encode([question])[0])
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT filename, heading, text, title, 1 - (embedding <=> %s::vector) AS score
                FROM chunks
                WHERE collection = %s
                ORDER BY embedding <=> %s::vector, filename, position
                LIMIT %s
                """,
                (question_vector, self.collection, question_vector, k),
            ).fetchall()
        return [SearchResult(float(score), Chunk(f, h, t, title)) for f, h, t, title, score in rows]
