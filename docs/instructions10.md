Don't push. Stop for my review.

Work only in C:\projects\crm-assitant. Preserve all current work, PostgreSQL 18, the existing volume and all stored vectors. No commits, live AI calls, server changes, data deletion or volume deletion.

Replace PostgreSQL trust authentication with password authentication. This is an existing initialized database: changing Compose environment variables alone is not enough.

1. Inspect the current setup and prepare a safe recovery path before changing authentication. Record the vector row count and fingerprint first.
2. Use a restricted, non-superuser application role. Keep tests isolated from the guide database. Set only the permissions each role actually needs, including startup/indexing behavior.
3. Ask me to enter any new passwords locally in .env. Never print secrets, put them in chat, or hardcode them. Handle special characters safely.
4. Remove trust authentication for network connections, including the effective rules in the existing data volume. Preserve a configuration backup and a local recovery route. Keep the database port unpublished.
5. Verify that missing/wrong passwords are rejected, the app connects with its own credentials, and application/test roles cannot access each other's databases. Show concise nonsecret outputs.
6. Prove the vector count and fingerprint are unchanged. Run the offline tests with generation mocked; report actual results and any failures without weakening tests.

Update CLAUDE.md with the authentication setup, verified results and remaining deployment requirements. Stop with a short summary. No commit, no push.