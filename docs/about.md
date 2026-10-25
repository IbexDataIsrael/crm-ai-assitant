CRM Assistant - Project Brief
2026-10-25. This is the shared big picture for every coding task on this project. If the code and this brief differ, report the discrepancy; don't silently change the intended behavior.
Rules for every task
Don't push. Don't commit. Stop for review after each task.
No live AI-provider calls in tests; mock external HTTP.
No production changes. Secrets come from environment settings and are never printed or logged.
Before coding, inspect the actual local files read-only and report what you find. Do not assume state from instructions.
Report actual results: changed files, real test output, honest failures. Never claim production readiness.
Label implementation choices as choices. Decisions not listed here stay open - ask, don't invent.
What we are building
A conversational helper for the CRM (Jewish Points / minicrm). A user types in natural language and can:
Ask how-to questions - answered from the CRM's user guides (Hebrew and English), with citations.
Ask live data questions - counts, lists and summaries from real CRM data ("how many apartments still need a visit?").
Perform actions - add or update records through a negotiate-and-confirm flow: the CRM resolves the record, asks for missing details, shows the exact proposal, and saves only after the user confirms. Nothing is saved while still being clarified.
Everything runs under the logged-in user's organization and permissions. The AI never chooses an organization and never gets unrestricted database access.
Clients: the site's popup now; possibly WhatsApp later (identity mapping not decided).
Architecture - two API boundaries
Client (popup, later WhatsApp) → AI service → CRM API.
The CRM (minicrm) owns: authentication, the user's organization and permissions, validation, business rules, resolving records, clarification questions, proposals, confirmation, saving, idempotency (a retry can never duplicate a record), and read-back proof.
The AI service (standalone) owns: understanding the request, asking for missing details in natural language, orchestrating calls to the CRM, and phrasing results. It forwards the user's identity to the CRM; it does not invent identity and does not own business rules.
Clients talk only to the AI service. CRM data and actions go through the CRM API, never direct CRM database access. The AI service separately uses its own embedding/vector store and AI provider.
Current state - what exists vs. what is proven
AI service (local folder C:\projects\crm-assitant):
Guide Q&A works locally: FastAPI + PostgreSQL/pgvector holding the two user guides.
Database access hardened: SCRAM password authentication, restricted app/test roles, no published database port. Verified locally (authentication checks passed, stored vectors unchanged).
The answer-language rule was strengthened (answer in the question's language even when the guide passages use another language). Proven in mocked tests only - the live re-test is still pending because the free AI provider returned overload (503) errors. Provider reliability is an open problem.
CRM (local folder C:\projects\sheepsidebay_crm):
Contact-draft action API built locally: POST /api/assistant/proposals and POST /api/assistant/proposals/{id}/confirm. User and organization come only from the login token. Missing details → needs_information. Valid details → a proposal bound to the exact fields, user and organization. Confirm → creates the draft exactly once; a retry returns the original result; changed details are rejected.
Full backend test suite passes (832 passed, 1 pre-existing skip). Built and tested locally. Not deployed.
In progress or not proven yet:
AI-service bridge to the CRM action API: task given, result not yet reported.
Popup: exists locally as a mock, not connected to anything.
Server deployment preparation: task running, result not yet reported; no deployment started.
WhatsApp: not mapped to CRM users.
The first finish line
One complete flow, end to end:
The user types "add a contact" → missing details are asked and answered → the exact proposal is shown → the user confirms → the draft is saved and read back → it appears in the CRM waiting for approval.
The finish line is the natural-language conversation, not a form: the user says what they want in their own words, the assistant asks for what is missing, and the CRM saves only after confirmation. The current intermediate slice is the structured-field bridge between the AI service and the CRM; natural-language understanding completes this same flow on top of it. Until the natural-language flow works end to end, the project is pieces, not a working assistant.
After the first flow (direction, not commitments)
Apartment Jewish-status action: needs a new scoped endpoint, version-aware proposals (apartment IDs change on every edit), and the "to knock" side effects handled. Note: unset status means knock, but the "na" value means don't knock - never map "unknown" to "na".
Live data questions: add proper count/grouping to the existing validated query path (today it caps at 200 rows, so totals above that are wrong).
Popup wiring to the AI service, then server deployment behind an authenticated service boundary.
Later, if wanted: semantic search over selected notes/documents, isolated per organization, with deletion handling. Not a bulk copy of the database into vectors.
Open decisions (the owner's, not yet made)
Whether to widen action permissions beyond the existing role guards, and to which roles.
The exact AI model/tool-calling and client-facing request/response contracts.
Apartment Jewish-status semantics: what "unknown", "na" and a mixed household should mean.
How long a proposal stays valid.
Whether private organization documents enter the vector store (needs per-organization isolation first).
AI provider and budget for production (the free tier has overload spikes).
WhatsApp identity: how a WhatsApp user maps to a CRM user.
