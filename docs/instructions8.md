1. Add PostgreSQL + pgvector to this project's Docker, with persistent storage and no public database port.
2. Store guide chunks, their context and 384-number vectors in real database rows. Search them with SQL cosine distance.
3. Reuse unchanged vectors; update changed chunks and remove obsolete ones.
4. Prove the stored vectors survive database-container recreation, then run the offline tests.
5. Report changed files, test results and how indexing/search work.

Keep the guides, chunking, router and answer model unchanged. No live AI calls, production changes, volume deletion, commits or pushes.