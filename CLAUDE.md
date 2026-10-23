# CRM Assistant - project notes

Last updated 23 October 2026.

## Goal

A conversational front door to the CRM: a volunteer says what they need, the assistant finds the record, makes the allowed change and answers questions from written instructions - instead of people working through screens.

## Rules for working in this project

- Never commit or push without explicit permission. Stop for review after each step.
- Work only in C:\projects\crm-assitant. Do not touch minicrm or any other project.
- No secrets in code, chat, the image or git. Keys arrive later through a vault link only.
- No paid or hosted services (AI APIs, databases, hosting) and no real CRM reads or changes without approval first.
- All documents in knowledge/ are fictional and marked "SAMPLE DATA - fictional". No customer data.
- Labels must stay honest: say what is real and what is a stub.

## Run it (Docker, PowerShell)

```powershell
cd C:\projects\crm-assitant
docker compose up -d --build     # build (first time ~5 min) and start
docker compose logs -f app       # watch startup; wait for "Application startup complete"
docker compose exec app python -m pytest -v   # run tests inside the container
docker compose down              # stop
```

Open http://127.0.0.1:8011 (local only; the container listens on 8000 inside).

Startup loads the embedding model before serving requests: about 40 s on first start after a build (model load itself ~18 s), and the container then uses about 1 GB of RAM. The model is baked into the image at /opt/hf-cache (outside /code, so the bind mount doesn't hide it) and loads offline - no network calls at runtime.

## Built (verified 23 October 2026)

- Chat page + FastAPI backend in Docker, port 8011.
- Routing: a keyword stub (app/planner.py) picks action / knowledge / clarify. It is NOT AI routing.
- Action route: placeholder CRM functions with demo data (app/crm.py). No CRM connection; nothing is changed.
- Knowledge route: real local semantic retrieval.
  - app/chunking.py splits knowledge/*.md into chunks that keep filename and heading. Chunk size is measured with the model's own tokenizer, including special tokens, against the model's real limit (128 tokens). Too-long text is split with a small overlap; anything that still can't fit raises an error instead of being silently truncated.
  - app/embeddings.py runs sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 locally on CPU (pinned revision). Small interface (max_tokens, count_tokens, encode) so another provider could replace it.
  - app/vectorstore.py keeps vectors in memory, rebuilt from knowledge/ at every start, and ranks with plain cosine similarity. No database.
  - app/knowledge.py returns the top 3 passages with similarity scores, filenames and headings. The reply is labeled "Local semantic retrieval - source passages, no AI-written answer yet".
- Pinned stack: Python 3.14, torch 2.14.1+cpu (official CPU wheel index, no GPU packages), sentence-transformers 6.1.0, transformers 5.18.0, pytest 9.1.1.
- Tests in tests/: chunking, retrieval with paraphrased questions, /chat routes. Known result: 13 of 15 pass. Two paraphrase questions rank the wrong passage first (the model ties "ill" to "unwell" in "During the visit", and doesn't link "hang up" to the phone-call steps in sample.md). These are left failing on purpose as an honest record.

This is retrieval, not full RAG: nothing writes an answer from the passages yet.

## Planned (not built)

1. Answer generation (next stage). Send the question plus the retrieved passages to a model and get a short answer that cites its sources. Model choice is open (Claude / Kimi / Gemini) and needs approval; any key comes through a vault link.
2. Real routing. Replace the keyword stub with a model call that returns a small JSON intent (function calling, same pattern as minicrm's ai_query).
3. CRM API actions. crm.py talks to the live minicrm API: read-only first (find contact) against a test org, writes later under the agreed rules - edits to existing records save directly (admins only), new contacts arrive as drafts, every change logged.
4. Persistent vector store, only if needed. Postgres + pgvector (Neon proposed) is a later decision; today's in-memory store is enough for a small corpus.
5. Later, only if wanted: deploy past localhost, chat box inside the CRM client, WhatsApp front door.

## Decided vs open

- Decided: separate project, Docker, the CRM stays untouched, conversation-first goal, one small reviewed step at a time, local embedding model for retrieval.
- Open (owner's decision): generation model, real corpus documents, vector store, any keys or spend.

## .NET analogies

- An embedding is a list of numbers, like coordinates on a map; similar meanings land near each other.
- Cosine similarity is the angle between two arrows: 1.0 means the same direction.
- pgvector would be a nearest-neighbour index, like SQL Server's spatial index for geography points, but for meaning.
