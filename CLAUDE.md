# CRM Assistant - project notes

Last updated 25 October 2026, later (docs/instructions8.md: guide chunks and vectors now in PostgreSQL + pgvector, synced incrementally). Before that, 25 October 2026 (chunks carry document title + section, docs/instructions7.md; first real-browser check; Gemini key rejected with 401, then a new key worked; .env.example placeholders emptied). Before that, 24 October 2026 (the reviewed English and Hebrew user guides are now the active knowledge; fictional samples moved to tests/fixtures). Before that, 23 Oct overnight: Claude adapter, page layout, docs/retrieval-known-failures.md, docs/CODE_WALKTHROUGH.md.

## Goal

A conversational front door to the CRM: a volunteer says what they need, the assistant finds the record, makes the allowed change and answers questions from written instructions - instead of people working through screens.

## Rules for working in this project

- Never commit or push without explicit permission. Stop for review after each step.
- Work only in C:\projects\crm-assitant. Do not touch minicrm or any other project.
- No secrets in code, chat, the image or git. Keys arrive later through a vault link only.
- No paid or hosted services (AI APIs, databases, hosting) and no real CRM reads or changes without approval first. Approved so far: Gemini API on the free tier only, for knowledge answers; and ONE two-question Claude Haiku 4.5 comparison, capped at $0.05 (max 2,000 input / 1,000 output tokens per call, counted before sending, no retries, no fallback, no top-ups) - approved but NOT yet run; it waits for the owner's go.
- Claude never reads, prints or asks for the real keys, and never reads .env. Live Gemini or Anthropic calls only when the owner says so.
- Active knowledge = exactly the two reviewed user guides listed in `ACTIVE_DOCUMENTS` (app/knowledge.py): knowledge/about_english.md ("Jewish Points CRM - User Guide") and knowledge/about_hebrew.md (the Hebrew version, "נקודות יהודיות CRM - מדריך למשתמש"). Owner's decision, 24 Oct 2026. Startup fails if either is missing; any other file in knowledge/ is skipped and logged "Not indexed: ...". The old "SAMPLE DATA - fictional" marker rule is removed. Don't add documents or rewrite the guides without the owner. No customer data in knowledge/ (the guides describe the product; they contain no people's records).
- The fictional sample documents (sample.md, visits.md, donations.md) now live in tests/fixtures/ and are used only by tests.
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

Database: a `db` service (pgvector/pgvector:0.8.2-pg18, pinned by digest) starts with the app; the app waits for its healthcheck. Data lives in the named volume `crm-assitant_pgdata`, which survives `docker compose down` and container recreation; only `docker compose down -v` or `docker volume rm` deletes it (never do that without the owner). No published port: reachable only from the app container on the compose network, so it uses "trust" login with no password - add one before ever publishing the port. Look at the rows: `docker compose exec db psql -U crm_assistant -d crm_assistant -c "SELECT filename, position, heading FROM chunks"`. Tests use a separate database, `crm_assistant_test`, on the same server.

Keys: `GEMINI_API_KEY` and `ANTHROPIC_API_KEY` go in .env (the owner enters them; Claude never reads the file). Gemini key (run yourself in a normal PowerShell window, not in Claude):

```powershell
cd C:\projects\crm-assitant
powershell -ExecutionPolicy Bypass -File .\scripts\set-gemini-key.ps1   # hidden input, writes .env
docker compose up -d --force-recreate                                  # restart so the container gets the key
docker compose exec app python -c "import os; print('key loaded:', bool(os.environ.get('GEMINI_API_KEY')))"
```

Without a key the app still works and shows retrieved passages only. To rebuild the index: `docker compose restart app`; the log ends with "Application startup complete". A tokenizer warning "140 > 128" during startup is harmless: it comes from the chunker measuring one long line before splitting it.

Startup loads the embedding model before serving requests: about 40 s on first start after a build (model load itself ~18 s), and the container then uses about 1 GB of RAM. The model is baked into the image at /opt/hf-cache (outside /code, so the bind mount doesn't hide it) and HF_HUB_OFFLINE=1 is set. UNVERIFIED: a live test on 23 Oct saw the model loader start 2 requests to huggingface.co (README.md and model info - no user text); not yet checked whether offline mode blocks them before they leave the container.

## Built (verified 23 October 2026)

- Chat page + FastAPI backend in Docker, port 8011.
- Routing: a bilingual (English/Hebrew) keyword stub (app/planner.py) picks action / knowledge / clarify. It is NOT AI routing. Rule (changed 24 Oct, docs/instructions6.md):
  - action: the message STARTS with a find / add / update command verb (English: find, search, add, create, update; Hebrew imperative and future request forms, e.g. מצא/חפש, הוסף/תוסיף/צור, עדכן/תעדכן), optionally after "please" / "בבקשה", and does not end with "?". Whole words only ("address" is not "add", "updates" is not "update"). A verb inside a question ("how do I add a contact?") is not an action. The planner also returns which demo action (`action`: find/add/update), and crm.pick_placeholder uses it.
  - clarify: empty or whitespace-only input only (the page already refuses to send an empty message; the server has no other input validation).
  - knowledge: everything else, so "what is jewishpoints" and greetings like "hello" search the guides. No list of question words.
  - Known limits: a polite question form without "?" ("can you add a contact") goes to knowledge; a command not starting with a verb ("Dana's phone - update it") goes to knowledge.
- Action route: placeholder CRM functions with demo data (app/crm.py). No CRM connection; nothing is changed.
- Knowledge route: real local semantic retrieval.
  - app/chunking.py splits the listed documents into chunks that keep filename, document title and section heading (changed 25 Oct, docs/instructions7.md). Headings: markdown `#` lines; a document with NO `#` lines (the two guides) uses short numbered lines instead ("1. What this system is": number + dot, at most 8 words, not ending in "."), and its short first line before the first section is the document title. In a document with `#` headings (the fixtures) numbered lines stay ordinary steps. Each chunk is embedded as "title > heading" + text (just the heading when there is no title, or the title is the heading). Headings and titles are context only: a section with no body makes no chunk, so a heading is never a passage by itself. The guide wording is unchanged. Chunk size is measured with the model's own tokenizer, including special tokens, against the model's real limit (128 tokens). Too-long text is split with a small overlap; anything that still can't fit raises an error instead of being silently truncated.
  - app/embeddings.py runs sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 locally on CPU (pinned revision). Small interface (max_tokens, count_tokens, encode) so another provider could replace it.
  - app/vectorstore.py stores chunks in PostgreSQL + pgvector (since 25 Oct, docs/instructions8.md; before: an in-memory list rebuilt and fully re-embedded at every start). Table `chunks`: collection ("guides" for the app), filename, position, title, heading, text, embed_text, content_hash (sha256 of model + revision + embed_text), embedding `vector(384)`, updated_at; key (collection, filename, position). Startup syncs the table with the guides: same hash at the same position -> kept; same hash elsewhere -> stored vector reused (moved chunk); otherwise embedded; rows whose chunk is gone -> deleted. One transaction, advisory lock per collection, logged as "Vector store 'guides': 54 chunks - N unchanged, N moved, N embedded, N removed". Search is SQL: `ORDER BY embedding <=> question` (cosine distance), score = 1 - distance; exact scan, no approximate (HNSW) index - not needed for 54 rows. A new connection per search, so a recreated database container is picked up. Verified 25 Oct: top-3 rankings and scores for 5 questions identical to the in-memory store; after `docker compose up -d --force-recreate db` the 54 rows and an md5 fingerprint of all vectors were unchanged, and the next app start logged "54 unchanged, 0 embedded".
  - chunking.py / vectorstore.py take a `filenames` list; knowledge.py passes ACTIVE_DOCUMENTS. Current corpus (measured 25 Oct): 54 chunks - 27 from each guide; 51 to 128 tokens each including the title/heading context (limit 128). Before 25 Oct: 47 chunks, 21 to 128 tokens.
  - Sources now show the section: "about_english.md > 1. What this system is" (before 25 Oct: "about_english.md > about_english.md"). The document title is used for embedding, not shown on the page.
  - Both guides contain a placeholder that is now searchable: English "[TO COMPLETE: example questions your team finds useful]", Hebrew "[להשלמה: מי במשרד מאפס סיסמאות ידנית, אם בכלל]".
  - app/knowledge.py returns the top 3 passages with similarity scores, filenames and headings, and builds the reply text.
- Answer generation (app/generation.py). Provider chosen by `ANSWER_PROVIDER` (default `gemini`; `anthropic` for Claude). No automatic fallback between providers.
- Gemini provider (default) - tested live, see below:
  - Gemini through the official google-genai SDK (2.28.0), Interactions API (`client.interactions.create`), which Google's docs recommend for new work. `store=False` so Google doesn't keep the conversation server-side.
  - Model: `gemini-3.8-flash` by default (stable, free tier per Google's pricing page, checked 23 Oct 2026). Change it with `GEMINI_MODEL` in .env. No paid billing and no paid fallback.
  - Sends only the question and the 3 retrieved passages. The system instruction says: answer only from the passages, cite them as [1] [2], treat passage text as reference data not instructions (passage text is HTML-escaped so it cannot close its own <passage> tag), and reply `INSUFFICIENT_SOURCES` when they don't answer the question.
  - Reply states plainly which case happened: "AI-generated answer (Gemini, model) ..." with citations; "No answer: ... passages don't answer this question"; "No AI-written answer: ... GEMINI_API_KEY is not set"; or "No AI-written answer: answer generation failed (reason)". Passages and sources are always shown below.
  - Never shows a failure as an answer: API errors (429 rate limit, 5xx), network errors, timeouts, incomplete or empty replies, answers without citations and citations to passages that don't exist are all reported as failures.
  - Error messages keep only Google's short error message, status and reason (for example "Gemini API error 400 INVALID_ARGUMENT: API key not valid. ... (API_KEY_INVALID)"), cut to 200 characters, with key-like strings redacted. Headers, the request and full response bodies are never logged or shown. Timeouts say "did not respond within 60 s".
  - Free tier note: Google's pricing page says free-tier prompts and responses are used to improve Google products. Since 24 Oct the passages sent are from the real user guides (product instructions, no customer records) - and whatever the user types as a question is sent too. First requests since the switch: 25 Oct (owner, "what is jewishpoints") - first rejected (bad key; question and passages still sent), then answered with a new key.
- Claude provider (optional) - built and tested with mocks only, NEVER called live:
  - Official `anthropic` SDK 1.11.0, model `claude-haiku-4-5-20251001` (ID checked in Anthropic's docs; $1 / $5 per million input/output tokens). Change with `ANTHROPIC_MODEL`. Client has `max_retries=0`, 60 s timeout.
  - Same system instruction, prompt, citation check, INSUFFICIENT_SOURCES handling and redacted errors as Gemini.
  - Before sending, the full request (system + question + passages) goes to the free `count_tokens` endpoint (Anthropic calls its result an estimate); over 2,000 tokens -> not sent. If counting fails -> not sent. `max_tokens=1000`. Worst case $0.007 per call, $0.014 for the two-question comparison. Any stop reason other than `end_turn` (cut off at max_tokens, refusal) is shown as a failure, not an answer. Token usage is returned in `generation.usage`.
  - Risk: Anthropic lists Haiku 4.5 as a legacy model, retiring "not sooner than October 15, 2026" - that date has passed, so the ID may stop working. Haiku 5.5 (`claude-haiku-5-5`, $0.10 / $0.50) is cheaper - owner's decision whether to switch.
- Page (static/index.html): labels say knowledge questions search "the CRM user guides (English and Hebrew)" (changed 24 Oct from "fictional sample documents"). A knowledge reply shows three separate parts - the answer box (green = AI-generated answer with provider, model and citations; amber = sources don't cover it / generation off; red = generation failed, "No answer was generated"), the source passages box (filename, heading, similarity score, a "cited" tag on cited passages), and the route/demo line. Request failures get their own red box. All server text is inserted as text, never HTML. Checked with mocked responses in a fake-DOM script, and on 25 Oct in a real browser by the owner (screenshot): the red "answer generation failed" box and the source passages box rendered as designed. The green (answer) and amber (insufficient sources) boxes have not been seen in a real browser yet.
- Key handling: `GEMINI_API_KEY` comes from the project's .env (ignored by Git and Docker builds) and is passed into the container at start by docker-compose.yml (`env_file`, optional). It is not in the image, the code, the tests or the logs. scripts/set-gemini-key.ps1 writes it with hidden input. .env.example holds names only. The .env file is plain text, not encrypted - any program running as your Windows user can read it.
- Pinned stack: PostgreSQL 18.4 + pgvector 0.8.2 (image), psycopg[binary] 3.3.6, Python 3.14, torch 2.14.1+cpu (official CPU wheel index, no GPU packages), sentence-transformers 6.1.0, transformers 5.18.0, google-genai 2.28.0, anthropic 1.11.0, pytest 9.1.1.
- Tests in tests/ (Gemini and Anthropic always mocked; a fixture removes both keys and fails any test that tries to create a real client): chunking (incl. the active-document list: only the two guides indexed, other files skipped, a missing guide raises), retrieval with paraphrased questions over the fixture samples, retrieval over the real guides (tests/test_guide_retrieval.py), generation (answer, citations, insufficient sources, missing key, 429/503/network failures, bad replies), error reporting and redaction, the Claude adapter (exact request, token limit before sending, cost cap, stop reasons, one HTTP request only, error formats), router rules (tests/test_planner.py), /chat routes (incl. "what is jewishpoints" and "מה זה נקודות יהודיות" -> knowledge over the real guides with Gemini mocked; "add a contact" / "הוסף איש קשר" -> add demo; empty input -> clarify). Retrieval and /chat tests that need known content use a store built from tests/fixtures (`sample_knowledge` fixture in conftest.py).
  - Result on 25 Oct 2026 after the pgvector change (same command): **143 of 146 pass** (22-38 s). 7 new tests in tests/test_vectorstore.py: first build stores every chunk with its hash; unchanged documents are not embedded again; a changed chunk is re-embedded and an obsolete row removed; a moved chunk reuses its stored vector; guide rows hold 384-number vectors and their title/heading context; the SQL score equals numpy cosine of the stored vector; tests point at crm_assistant_test. The same 3 failures as below, with the same scores.
  - Result on 25 Oct 2026 after the chunking change (`docker compose exec -T app python -m pytest -p no:cacheprovider -q`): **136 of 139 pass**, in 22 s (24 Oct: 128 of 131). 8 new tests: guide chunks keep title + section, no heading-only chunks, numbered lines stay steps in `#` documents, and "what is" in both languages must retrieve the explanation first from the matching guide. No assertion was weakened. The 3 failures, left failing on purpose as an honest record:
    0. FIXED by the change (not by editing the test): "I'm ill and won't make tomorrow's appointment..." now ranks visits.md "Cancelling a visit" first (0.311; "During the visit" 0.243). The fixture chunks gained the document title in their embedding.
    1. Sample fixtures, known since 23 Oct, still failing (now 0.134 "After the visit" first): "What should I jot down once I hang up with someone?" - the top 3 are all visits.md ("After the visit" 0.158 first); the expected sample.md chunk is not in the top 3. Details: docs/retrieval-known-failures.md.
    2. Real guides, known since 24 Oct, WORSE after the change: the Hebrew question "שכחתי את הסיסמה שלי, מה עושים?" ranks the ENGLISH guide's password passage first (0.384) and the expected Hebrew passage ("שכחתם סיסמה...") is now THIRD (0.315; before: second, 0.417). The right fact is retrieved, but in the other language first.
    3. New, real guides: "what is jewishpoints" ranks the HEBREW guide's explanation first (0.597). The test (expectation written before the first run) requires the English explanation ("A bilingual (Hebrew/English) CRM...") first; it scores 0.543 and is not in the top 3. Same mixed-language problem as 2.
  - "what is jewishpoints" / "מה זה נקודות יהודיות" (local retrieval only). Before 25 Oct, for both questions the top 3 held NO explanation: ranks 1-2 were the title + heading-only chunks (0.607 / 0.550 and 0.604 / 0.565), rank 3 the Hebrew custom-fields passage. After the change (25 Oct):
    - "what is jewishpoints": 1. about_hebrew.md "1. מהי המערכת הזו?" 0.597 - the Hebrew explanation ("מערכת CRM דו-לשונית ..."); 2. about_english.md "4. Everyday work" 0.566 - "Places: Areas, Buildings, Apartments" (not relevant); 3. about_hebrew.md "4. עבודה יומיומית" 0.562 - donations/reports (not relevant).
    - "מה זה נקודות יהודיות": 1. about_hebrew.md "1. מהי המערכת הזו?" 0.567 - the Hebrew explanation; 2. about_hebrew.md "4. עבודה יומיומית" 0.561 - donations/reports; 3. about_english.md "4. Everyday work" 0.536 - "Places".
    - So rank 1 now contains the explanation for both questions, but ranks 2-3 are weak: the title "Jewish Points CRM - User Guide" is in every chunk's embedding, so the product name matches every chunk about equally. Measured but NOT adopted: heading-only embedding (no title) also put the Hebrew explanation first for the English question and lost the document context; title only on section 1 put both explanations at the top, but that special-cases one section, close to hardcoding.
  - The English guide question passes: "Why can't I see a contact that the assistant added?" -> rank 1 about_english.md 0.385, the chunk with "Pending approval"; rank 2 0.316 "Add a new contact"; rank 3 0.300 (contact display) - all English (24 Oct: 0.393, then sign-in/roles and the Hebrew duplicate).
  - Observed, not explained: this multilingual model often puts the same fact from both guides in the top 3, so the 3 passages may mix languages and repeat one fact. Possible fixes (owner's decision): search only the guide matching the question's language, or merge same-fact passages.

## Live test results (23 October 2026, free-plan key)

- Donation question ("How do I deal with money a supporter gives me?"): WORKED in 4 s. Grounded 3-sentence answer citing [1] donations.md > Accepting a cash donation. So `thinking_level: "low"` and `max_output_tokens: 1024` are accepted.
- Office-alarm question ("How do I reset the office alarm?"): timed out twice at the 60 s server limit, and returned a 400 with thinking_level "minimal". With thinking_level "low" and a one-off 120 s timeout it WORKED: "No answer: ... the passages below don't answer this question" (one Gemini request; 54.9 s including a fresh embedding-model load). Off-topic questions are much slower than answerable ones, so they may still time out at the server's 60 s default.
- Bugs the live run found (mocks had missed them), now fixed with regression tests:
  - The client object was garbage-collected before the request was sent ("client has been closed"). Fixed by keeping a reference.
  - The Interactions API raises its own error classes (status in `.status_code`), not `google.genai.errors.APIError`, so real API errors were mislabelled "could not reach". Fixed by reading the status by attribute.
  - The SDK silently retried 429/5xx/connection errors (4 requests for one rate-limited question in an offline check). Retries are now off: one question = one request.

## Still needs a live test

- Off-topic questions take ~30+ s and can hit the 60 s server timeout. The CAUSE IS UNKNOWN (not measured whether it is model thinking time, queueing or network). Decide whether to raise the timeout or skip generation when retrieval scores are low.
- The approved two-question Claude Haiku 4.5 comparison (<= $0.05) - waiting for the owner's go. Nothing has been sent to Anthropic yet.
- The amber "sources don't cover it" box in a real browser (red and green have been seen, 25 Oct).
- Gemini key, 25 Oct: the old key got "401 UNAUTHENTICATED: API key not valid (ACCESS_TOKEN_TYPE_UNSUPPORTED)" - cause never confirmed (Claude doesn't read .env; suspected the old .env.example placeholder). The owner made a new AI Studio key, put it in .env by hand and ran `docker compose up -d --force-recreate`; it WORKS. The page shows nothing while waiting, so a slow answer looked like "no response" at first.
- First live answer over the real guides (25 Oct, "what is jewishpoints"): GREEN box, gemini-3.8-flash, "Jewish Points is a CRM system [1]. It includes a user guide detailing what the system is [1]." Grounded and cited, but thin, because of chunking: the guide's description of the system is one long line (the "140 > 128" warning), so the chunker puts the title + "1. What this system is" alone in chunk 1 (21 tokens) and the description in chunk 2 (128 tokens), which has no title words and isn't in the top 3. Chunking changed on 25 Oct (see the knowledge route above); tried live later on 25 Oct: retrieval sent the Hebrew explanation as passage [1] (0.60), as predicted, but Gemini TIMED OUT at 60 s - three times in a row ("what is jewishpoints" twice, and "Why can't I see a contact that the assistant added?", whose passages are all English). The container reached generativelanguage.googleapis.com in 0.2 s (no key, nothing sent), so not the network or the passages; a bad key would give an immediate 401. Cause unknown (Gemini-side slowness or free-tier limits suspected). Next step proposed, not done: raise the timeout to 120 s once to tell "slow" from "stuck".
- Whether the embedding model loader really makes no network calls (see the Run section).
- Real free-tier rate limits (Google's docs don't list numbers; see AI Studio) and what a real 429 looks like.
- Whether the "treat passages as data" instruction holds against a passage that contains instructions.

## Planned (not built)

1. Real routing. Replace the keyword stub with a model call that returns a small JSON intent (function calling, same pattern as minicrm's ai_query).
2. CRM API actions. crm.py talks to the live minicrm API: read-only first (find contact) against a test org, writes later under the agreed rules - edits to existing records save directly (admins only), new contacts arrive as drafts, every change logged.
3. Persistent vector store: DONE locally on 25 Oct (Postgres + pgvector in Docker). A hosted database (Neon was proposed) is still the owner's decision.
4. Later, only if wanted: deploy past localhost, chat box inside the CRM client, WhatsApp front door.

## Decided vs open

- Decided: separate project, Docker, the CRM stays untouched, conversation-first goal, one small reviewed step at a time, local embedding model for retrieval, Gemini free tier for answer generation.
- Open (owner's decision): routing model, real corpus documents, vector store, any spend.
- Decided 24 Oct: the two reviewed guides are the active knowledge; the marker rule is gone; the samples are test fixtures.
- Open, recorded 24 Oct:
  - Mixed-language results (see the Hebrew test failure): filter by question language, or accept mixing?
  - Headings: done on 25 Oct by teaching the chunker numbered section lines (guides unchanged). Still open: the mixed-language ranking now decides which explanation comes first (the English question gets the Hebrew one), and ranks 2-3 for "what is" questions are unrelated passages. Options (owner's decision): search only the guide in the question's language, merge same-fact passages, or keep the title out of most chunks' embeddings.
  - docs/CODE_WALKTHROUGH.md and docs/retrieval-known-failures.md still describe the old 11-chunk sample corpus in knowledge/ (the retrieval doc's numbers still hold for tests/fixtures). Not updated in this step.
  - Real guide passages go to Gemini's free tier (used by Google to improve products). This first happened on 25 Oct, when the owner asked a live question (the key was rejected, but the request was sent). Still recorded as the owner's call.
- Open, recorded during the 23 Oct overnight run (nothing was decided for the owner):
  - Stay on Haiku 4.5 (legacy, past its earliest retirement date) or switch the comparison to Haiku 5.5?
  - Off-topic Gemini timeouts: raise the 60 s limit, or skip generation when every retrieval score is low?
  - How to check that the embedding loader makes no network calls (for example, run the container with no network once).
  - The .gitignore line for conversation exports is still uncommitted, along with everything else from this run.

## .NET analogies

- An embedding is a list of numbers, like coordinates on a map; similar meanings land near each other.
- Cosine similarity is the angle between two arrows: 1.0 means the same direction.
- pgvector would be a nearest-neighbour index, like SQL Server's spatial index for geography points, but for meaning.
