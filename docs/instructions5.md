Don't push or commit. No live API calls, including Gemini and Anthropic, im going to sleep :) while I sleep. Work only in C:\projects\crm-assitant. Don't read or print .env or secrets. Don't ask questions; record unresolved decisions for my review.

Continue from the actual current state. Don't redo completed work or interfere with another running process.

1. Run the offline test suite. Finish the pending error-handler fixes and optional Haiku 4.5 answer adapter if incomplete. Keep Gemini available, existing retrieval and port8011 unchanged. Use mocked API responses only, even if keys are configured. No new paid calls, provider fallbacks or billing changes.

2. Investigate the two known retrieval failures using the existing local embedding model. Write docs/retrieval-known-failures.md showing questions, expected sources, actual rankings and scores. Separate observed facts from possible explanations. Don't change document wording, tests or retrieval just to make the failures disappear.

3. Make the page clearly separate generated answers, source passages and errors. Preserve accurate demo/stub labels. Verify using mocked generation, not live calls. Visually inspect if browser access is available; otherwise report that visual inspection is pending.

4. Write docs/CODE_WALKTHROUGH.md in simple English for an experienced .NET developer learning this code. Follow one donation question through the actual files, from HTTP request to retrieval to generation to displayed sources. Explain chunking, embeddings, cosine ranking, in-memory storage, prompts, each provider and citation checks. Use small examples and fitting .NET analogies. Include a plain-text flow diagram. Explain why citations do not by themselves prove an answer correct. Clearly label real behavior, stubs and fictional data. No personal names or secrets.

5. Update CLAUDE.md from actual files and observed results, not assumptions. Record test counts, known retrieval misses, unknown Gemini timeout cause, any live-test results already recorded and what is still unverified. Don't rerun live tests. Keep the approved but pending two-question Anthropic comparison capped at $0.05 and awaiting my go.

Don't import the draft English/Hebrew guides into the active corpus. Don't touch minicrm, production, auth, deployment, other projects or database setup. Don't add unrelated dependencies.

Stop with a short summary of changed files, tests, remaining problems and the first thing I should inspect when I return.