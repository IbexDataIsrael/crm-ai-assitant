# Retrieval: the two known failures

Measured 23 October 2026 inside the Docker container, with the real local model
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (pinned revision
`e8f8c211`), the current chunking, and plain cosine similarity. No document wording,
test or retrieval setting was changed to produce or hide these results. All documents
are fictional sample data.

The searchable corpus is 11 chunks from three files:

| File | Chunk (heading) | Tokens (incl. special) |
|---|---|---|
| donations.md | SAMPLE DATA - fictional donation instructions | 27 |
| donations.md | Accepting a cash donation | 59 |
| donations.md | Receipts | 50 |
| donations.md | Donated goods | 50 |
| donations.md | Returning a donation | 42 |
| sample.md | SAMPLE DATA - fictional volunteer instructions | 93 |
| visits.md | SAMPLE DATA - fictional home visit instructions | 28 |
| visits.md | Before a home visit | 54 |
| visits.md | During the visit | 54 |
| visits.md | After the visit | 36 |
| visits.md | Cancelling a visit | 48 |

The tests that fail are in `tests/test_retrieval.py`. Each one requires the expected chunk to rank **first**.

---

## Failure 1 - "I'm ill and won't make tomorrow's appointment at someone's place - who should I let know?"

**Expected first:** visits.md > Cancelling a visit

### Observed

| Rank | Score | Chunk |
|---|---|---|
| 1 | 0.408 | visits.md > During the visit |
| **2** | **0.400** | **visits.md > Cancelling a visit (expected)** |
| 3 | 0.340 | visits.md > Before a home visit |
| 4 | 0.323 | visits.md > After the visit |
| 5 | 0.154 | sample.md > SAMPLE DATA - fictional volunteer instructions |
| 6-11 | 0.144 to 0.028 | the remaining donations.md and visits.md chunks |

- The expected chunk is second, 0.008 behind the first. It is in the top 3, so the app does show it to the user (and to the answer model) - just not first.
- All four visits.md section chunks rank above everything else.

Each line scored on its own against the question:

| Score | Chunk | Line |
|---|---|---|
| 0.195 | During the visit | 1. Wear your volunteer badge where it can be seen. |
| 0.252 | During the visit | 2. Stay in the shared rooms of the home and keep the visit under one hour. |
| **0.461** | During the visit | 3. If anyone seems unwell or unsafe, leave politely and call the coordinator. |
| **0.470** | Cancelling a visit | 1. If you cannot attend, phone the coordinator at least three hours before the visit. |
| 0.382 | Cancelling a visit | 2. The coordinator will tell the family and find a replacement volunteer. |
| 0.099 | Cancelling a visit | 3. Do not cancel by text message only. |

Short probes (best-matching chunk):

| Probe | Best chunk | Score |
|---|---|---|
| "I am sick" | During the visit | 0.132 |
| "unwell" | During the visit | 0.168 |
| "I cannot attend the visit" | Cancelling a visit | 0.528 |

### Possible explanations (not proven)

- The question mixes two ideas: being ill, and cancelling. "Ill" sits close to "unwell" in the
  "During the visit" chunk; "won't make the appointment / who should I let know" sits close to the
  "Cancelling" chunk. The two best single lines (0.461 vs 0.470) are almost tied, so a small
  difference in the rest of each chunk decides the order.
- The question never says "visit" or "cancel"; it uses "appointment at someone's place". When the
  question says "I cannot attend the visit" the expected chunk wins clearly (0.528).
- A near-tie like this is the kind of case a reranker or a larger embedding model might resolve;
  that has not been tried.

---

## Failure 2 - "What should I jot down once I hang up with someone?"

**Expected first:** sample.md > SAMPLE DATA - fictional volunteer instructions (the "After each call, write a short note" step)

### Observed

| Rank | Score | Chunk |
|---|---|---|
| 1 | 0.158 | visits.md > After the visit |
| 2 | 0.140 | visits.md > Before a home visit |
| 3 | 0.110 | visits.md > During the visit |
| 4 | 0.104 | visits.md > Cancelling a visit |
| 5 | 0.078 | donations.md > Donated goods |
| **6** | **0.042** | **sample.md > SAMPLE DATA - fictional volunteer instructions (expected)** |
| 7-11 | -0.012 to -0.168 | the rest |

- The expected chunk is sixth, so it is **not** in the top 3: the user and the answer model never see it.
- Every score is low (best 0.158). For comparison, the donation question that works scores 0.44.

Each line of the top chunk and the expected chunk, scored on its own:

| Score | Chunk | Line |
|---|---|---|
| 0.177 | After the visit | 1. Log the visit on the contact's card the same day. |
| 0.134 | After the visit | 2. Note any needs the family mentioned, such as food, transport or paperwork. |
| -0.105 | sample.md | These instructions are made up for the demo. They are not real procedures. |
| 0.003 | sample.md | 1. Sign in at the front desk when you arrive. |
| 0.066 | sample.md | 2. Check the daily task board for your assignment. |
| 0.114 | sample.md | 3. Before calling a contact, read the notes on their card. |
| **0.245** | sample.md | **4. After each call, write a short note about what was discussed.** |
| 0.042 | sample.md | 5. If you are unsure about anything, ask the shift coordinator. |

- The right line, scored alone (0.245), beats every whole chunk (best 0.158). Inside its chunk
  the same line is mixed with five unrelated lines and the chunk scores 0.042.
- sample.md is one 93-token chunk because it has no `##` sub-headings, and it fits under the
  model's 128-token limit, so the chunker keeps it whole.

Short probes (best-matching chunk):

| Probe | Best chunk | Score |
|---|---|---|
| "phone call" | After the visit | 0.332 |
| "hang up the phone" | After the visit | 0.270 |
| "after a phone call, write a note" | After the visit | 0.429 |

### Possible explanations (not proven)

- **Dilution:** one chunk vector stands for the whole chunk. A single relevant line among six
  unrelated ones pulls the chunk's vector away from the question. The per-line scores above fit
  this explanation.
- **Note-taking pull:** "After the visit" talks about logging and noting things, which is close
  to "jot down"; that may explain why it wins even though it is about visits, not calls.
- **Idiom:** even direct probes about phone calls land on "After the visit", not on sample.md,
  so this small model may not connect "hang up" / "phone call" strongly to "call" in "After each
  call". Splitting chunks smaller was tried once earlier (40-token chunks): this question still
  did not find sample.md, so dilution alone may not be the whole story.

---

## What was deliberately not done

- Document wording was not changed (for example, adding `##` headings to sample.md would likely
  change failure 2, but that would be tuning the documents to the test).
- The tests still require rank 1 and still fail. They record a real limitation.
- No other model, reranker or hybrid keyword search was tried. Those are options for later, each
  needing its own decision.

## Side note: an extra file was briefly searchable

On 23 October a draft guide (`knowledge/about_hebrew.md`, not fictional, no SAMPLE DATA
marker) was placed in `knowledge/`. Because it ends in `.md`, the loader indexed it (25 extra
chunks) until the loader was changed to index only files containing the
"SAMPLE DATA - fictional" marker. With that file included, the top 3 for failure 1 were
unchanged; for failure 2 one of its chunks ranked 6th, just above the expected chunk. The
numbers above are from the 11-chunk corpus of fictional files only.
