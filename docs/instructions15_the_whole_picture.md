Spec: chat login and add-contact conversation (25 October 2026)
Rules for every task: Don't push. Don't commit. Stop for review after each task. No live AI-provider calls in tests; mock external HTTP. No production writes. Secrets are never printed, logged or stored.
Goal
Turn the existing chat page into the first complete conversation: log in on the page, pick what to do from a quick menu, then add a contact in plain language, with an explicit yes before anything is saved. Guide Q&A keeps working as it does today.
What exists today
Chat page + FastAPI in Docker, served on 127.0.0.1:8011. /chat answers guide questions; its action route still returns demo data.
Bridge endpoints, proven live on 25 October: POST /crm/contact-drafts/proposals and POST /crm/contact-drafts/proposals/{proposal_id}/confirm. Both need Authorization: Bearer <user's CRM token>. The token is forwarded only to CRM_API_URL; it is never logged, stored, returned or sent to an AI provider. A request body with organization_id or user_id is refused (422).
CRM login: POST /api/auth/token, form-encoded: username (a username or an email), password, optional organization_id. The answer has access_token. If the login belongs to more than one organization, the answer is requires_org_selection: true with the list of organizations; log in again with organization_id.
Bridge answers. Propose: 200 needs_information (missing_fields, invalid_fields, normalized fields to resubmit; the service keeps no conversation state), 201 proposal (proposal_id, payload_hash, expires_at, the exact draft), 422 invalid. Confirm: 201 created (replayed=false), 200 with replayed=true on a same-hash retry, 504 uncertain (retry-safe with the same proposal_id and payload_hash; never make a new proposal), 410 expired, 404 unknown_proposal, 409 (payload_mismatch or retryable conflict), 401, 403.
Build
1. Login on the existing chat page
Add a login form to the existing chat page. Not a new page, not a terminal client.
Login path: the browser posts the form to the AI service (same origin as the chat page, so the browser makes no cross-origin call), and the AI service sends the login request to the CRM address in CRM_API_URL with redirects disabled; redirects with credentials are never followed. The proxy does not log or persist credentials or tokens. This local login proxy is a design choice - inspect the real CRM login contract before coding it.
Credentials are typed into the form only, never into the chat conversation. The password is used once, for the login request, and is never stored, logged or sent anywhere else.
On success keep only the JWT, in browser memory: not in localStorage, sessionStorage or cookies, and not in global server-side user state. The chat sends it as Authorization: Bearer to the bridge endpoints. The conversation and its pending proposal state belong to that token's user and organization; clear all pending state on logout, on a token change and on any 401. No second login, no hardcoded credentials, no credentials in .env.
Multi-organization: if the answer is requires_org_selection, show the returned organizations, let the user pick one, and log in again with organization_id.
Logout drops the token from memory.
2. Conversation: create a contact draft
Start from a quick menu, not free text. When the chat opens after login, show "What do you want to do?" with three choices: Create a contact, Update a contact, Ask a question. The menu choice sets the intent; details are collected conversationally afterwards - the user never has to type "I want...".
Create a contact: the conversation below; replace the action route's demo data for this one action only.
Update a contact: not built - shown clearly unavailable (for example disabled, marked "coming later"); it must never call the bridge or pretend to work. A real update needs separate CRM contracts and is not this task.
Ask a question: the existing guide Q&A, unchanged.
The model does understanding, clarifying and phrasing only. The intent comes from the menu choice; the model returns structured fields. It never calls the CRM or the bridge directly. All saving goes through the bridge.
Fields: first_name, last_name, phone, email, address. The CRM decides what is missing or invalid. The client holds the fields between turns.
Loop: send the collected fields to the bridge. On needs_information, ask only for missing_fields. For invalid_fields, rephrase the per-field error and re-ask only that field.
When the bridge returns a proposal, show the exact draft back in words and ask for an explicit yes. Confirm is authorized only by a clear yes to the exact proposal currently shown - never by the model's read of intent. Any change to the fields invalidates the old proposal: re-propose with the corrected fields, show the new exact draft, and require a fresh yes. A bare "no" cancels the pending confirm; do not re-propose unless the user asks.
On an explicit yes, confirm with the stored proposal_id and payload_hash.
Outcomes: created - say what was saved, from the confirm answer, and that it is a draft waiting for approval in the CRM. replayed=true - the same contact, no duplicate; say so. uncertain - do not make a new proposal; retry with the same proposal_id and payload_hash. expired - offer to start the proposal again.
Answer in the user's language (Hebrew or English), per the existing language rule.
3. Token and secret handling
The token goes only in the Authorization header, only to the bridge. Never in logs, responses, chat history shown to the model, or prompts to the AI provider. Extend the existing token-leak tests to the conversation layer.
The password exists only between the form and the login request. Never logged, stored, or sent to the model.
4. Model and provider
Keep the current provider configuration and default model. No paid calls. Any provider or model change is a separate decision, not part of this task.
5. Tests (all mocked; the provider and CRM HTTP fixtures stay enforced)
Login: success, wrong credentials, multi-organization selection.
Menu: each choice sets the intent without free text; "Update a contact" answers that it is not available yet and never calls the bridge; "Ask a question" still reaches guide Q&A.
Missing-field loop and invalid-field re-ask.
Confirm is never called without an explicit yes to the current proposal: an ambiguous answer, a "no", or a yes given before edited fields all leave confirm uncalled; a stale proposal can never be confirmed.
User and session isolation: one login's pending proposal and fields are never visible to, or confirmable by, another token or session.
A lost confirm answer: the retry returns the same contact, no duplicate.
The token never appears in logs, responses or model prompts.
Hebrew and English phrasing.
Design choices (decide before coding, write down what you picked)
The exact wording shown for each CRM error code. The CRM returns codes only; the UI translates them.
Known limits and gaps (do not fix in this task)
The bridge tells payload_mismatch from a retryable conflict by the CRM's exact error wording. Handle both conservatively.
The AI service has no authentication of its own and binds to localhost. Exposing it is unresolved and out of scope.
Proposals have no cancel endpoint; they expire on their own.
While CRM_API_URL points at production, a confirmed draft is real production data. Build and tests run fully mocked; any live run is started by me, when I choose.
Not in this task
CRM popup (future: it should reuse the existing CRM login token, so there is no second login; read the frontend AuthProvider to ground that design before building it), deployment, new CRM endpoints, find/update/delete actions, model or provider changes.
Done when
Login works on the chat page with form-typed credentials, including multi-organization selection.
The quick menu opens the conversation: Create starts the add-contact flow, Ask reaches guide Q&A, and Update is visibly unavailable.
A full add-contact conversation works: details, exact proposal, explicit yes, confirm, and an answer saying what was saved.
No new test failures; the 3 known retrieval failures stay unchanged and are reported as-is. No live provider or CRM calls in tests.
Nothing committed, nothing pushed. Stop for review.
