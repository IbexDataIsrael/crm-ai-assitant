Don't push. Stop for my review.
Work only in C:\projects\crm-assitant. No commits, live AI calls, secrets, or database changes.

Fix the chunking/retrieval problem shown by "what is jewishpoints": the top passage contains only the document title and "1. What this system is", while the actual explanation beginning "A bilingual" is in a separate chunk that isn't retrieved.

Inspect the actual English and Hebrew guide structure and app/chunking.py. Preserve the reviewed guide wording. Make chunks carry document and section context, keep headings with useful body text, and avoid standalone title/heading-only retrieval results. Keep the embedding model's 128-token limit, including metadata and special tokens. Don't solve this by increasing the limit or hardcoding an answer to this question.

Verify offline, with generation mocked:
1. Chunks containing the introductory explanation retain the product and section context.
2. Measure the actual top 3 passages for "what is jewishpoints" and "מה זה נקודות יהודיות". Check whether they contain the explanation, not just its heading. Report the passages and scores.
3. Run the full test suite. Report all failures honestly, including whether the 3 earlier retrieval failures changed. Don't weaken assertions just to pass.

Update CLAUDE.md with what changed and what remains unverified. Stop with a short summary and test results. No commit, no push.