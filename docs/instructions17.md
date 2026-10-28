Spec: login enforcement for the AI assistant (public access)
Don't push. Don't commit. Stop for my review.
This is the second spec. The server-profile spec comes first and stays unchanged; keep that work as it is. Inspect what already exists, keep it, and add only what is missing. All work is local: build and run mock tests on this machine. No deployment, DNS, Caddy changes, server commands, live CRM calls, live AI calls or public exposure. Never delete an existing Docker volume.
Goal
The assistant will be reachable from the internet. Today the browser hides the menu before login, but direct HTTP requests without a token still reach the chat routes, and each such request can spend the AI provider's money. Before public access, the server itself must require a valid login on every route that does AI work or reads or changes data. Three exceptions stay open on purpose: POST /auth/login (it is the login, protected by the attempt limits below), static page assets, and a minimal /health check.
1. Check how the CRM verifies a login (read-only)
Read the CRM project at C:\projects\sheepsidebay_crm without changing anything. Confirm that GET /api/auth/me exists and that it checks the token by decoding it and looking up the user in the database, rejecting missing, disabled or deleted users. Report the exact handler and what it does. Design choice: we check tokens by calling the CRM, because only the CRM knows whether a user is still active. Do not copy the CRM's signing secret into this project. Note in the report: the live CRM on the server has not been checked yet; this confirms the contract from local code only.
2. Require login on all assistant routes
/assistant/turn, every intent: require a Bearer token. Before any AI work, send it to CRM_API_URL + /api/auth/me. Only a 200 from the CRM lets the request continue.
/chat: same rule. Design choice: Ask was open on purpose before; it now requires login because the assistant will be public. State this change in the report.
Keep forwarding the user's own token to the CRM for actions (create contact now, apartment edit later). The apartment-edit flow (propose, confirm, save) must sit behind the same check when it is built.
Keep the browser flow unchanged: the user's token stays in browser memory. Do not add any shared service secret to the browser or to this service.
3. Error behavior: fail closed and tell the truth
CRM answers 401: the login is no longer valid. Answer 401; the client asks the user to log in again.
CRM answers 403: permission problem, not a login problem. Answer 403.
CRM times out, is unreachable or answers 5xx: do no AI work, answer "service temporarily unavailable, try again", and keep the user's session. Never show an outage as a wrong password or a logout.
No validation cache. Design choice: one extra CRM call per chat turn is acceptable; a cache is a possible later decision.
Treat a CRM answer as valid only when it is a 200 with the expected user data. A 200 with a missing or malformed body, or any unexpected answer, is a failure: no AI work, answer "temporarily unavailable".
Send the token only to the configured CRM address. Follow no redirects. Never write the token to logs.
4. Slow down login guessing
Add per-IP rate limiting on /auth/login and on the chat routes above, with fail-closed defaults. The CRM records login attempts today but does not slow them down. The exact mechanism is a labeled choice. The service runs behind a reverse proxy, so the client address must come only from the trusted proxy: never accept an X-Forwarded-For header that a client supplied directly, and never treat the proxy's own address as the user - that would count everyone as one client or let anyone reset their own limit. Keep the existing proxy rules: never store the password, set no cookies, follow no redirects.
5. API docs pages
FastAPI publishes /docs and /openapi.json, which describe the whole API. Design choice for the server profile: turn them off and document how to turn them back on for local work. Report which behavior was chosen.
6. Checks and report
Local build and mock tests only. Mock every CRM and AI call.
Prove: a request without a token gets 401 and no AI work happens; a valid token works; a CRM outage answers "temporarily unavailable" and keeps the session; repeated login attempts get rejected; a spoofed X-Forwarded-For header does not change which client is counted.
Report which parts of the server-profile spec are already done and list every remaining local change, so each change can be reviewed before any push.
The public address name and the AI provider choice are decided later at deployment. This task does not need them.