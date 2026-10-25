"""Offline check for the embedding model: does loading and using it touch the network?

Runs the app's own model path - load the model (app.embeddings.get_embedder),
chunk both guides with its tokenizer, embed every chunk and one question -
while a Python audit hook records every socket connect and DNS lookup the
process attempts. No database, no AI provider, no CRM.

    docker run --rm --network none crm-assistant-server-app python scripts/offline_model_check.py
    docker compose -f docker-compose.server.yml exec app python scripts/offline_model_check.py

Prints PASS only if the model loaded, the vectors have the expected shape and
not one network attempt was recorded. Exit code 0 = PASS, 1 = FAIL.
"""

import os
import sys
from pathlib import Path

attempts = []


def audit(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.sendto"):
        # Keep only the target, never any data.
        attempts.append((event, repr(args[1] if event in ("socket.connect", "socket.sendto") else args[0])[:80]))


sys.addaudithook(audit)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.chunking import chunk_folder  # noqa: E402
from app.embeddings import get_embedder  # noqa: E402
from app.knowledge import ACTIVE_DOCUMENTS, KNOWLEDGE_DIR  # noqa: E402

print("HF_HUB_OFFLINE =", os.environ.get("HF_HUB_OFFLINE", "(not set)"))
failures = []
try:
    embedder = get_embedder()
    chunks = chunk_folder(KNOWLEDGE_DIR, embedder.count_tokens, embedder.max_tokens, ACTIVE_DOCUMENTS)
    vectors = embedder.encode([c.embed_text for c in chunks])
    question = embedder.encode(["what is jewishpoints"])
    print(f"model loaded in {embedder.load_seconds:.1f}s; {len(chunks)} guide chunks -> vectors {vectors.shape}; question {question.shape}")
    if vectors.shape != (len(chunks), 384) or question.shape != (1, 384):
        failures.append("unexpected vector shape")
except Exception as error:  # report, don't hide
    failures.append(f"model path failed: {type(error).__name__}: {str(error)[:160]}")

print(f"network attempts recorded: {len(attempts)}")
for event, target in attempts[:10]:
    print(f"  {event} {target}")
if attempts:
    failures.append(f"{len(attempts)} network attempt(s)")

print("PASS" if not failures else "FAIL: " + "; ".join(failures))
sys.exit(0 if not failures else 1)
