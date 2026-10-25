Don't push. Don't commit. Stop for my review.
Preserve all current work. No live CRM or AI-provider calls, no deployment. Mock external HTTP in tests.

GOAL
The standalone AI service must consume the CRM's contact-draft API and expose that flow to its clients. These are two separate contracts:
Client -> AI service -> CRM API.
The CRM owns permissions, organization scoping, validation and saving. This task builds the AI-service bridge, not the popup.

EXISTING CRM CONTRACT
Read the actual router, schemas and tests under C:\projects\sheepsidebay_crm without changing them. The operations are:
POST /api/assistant/proposals
POST /api/assistant/proposals/{proposal_id}/confirm
Use the exact field names and response states found in that code, not guessed schemas.

CONFIGURATION
Use CRM_API_URL from the environment as the CRM base address. No hardcoded server address and no fallback to production. Add its name to .env.example and document setup. Handle trailing slashes consistently. Reject invalid addresses or credentials embedded in the URL. Missing configuration must fail clearly without exposing secrets.

AUTHENTICATION
The client supplies the actual user's CRM bearer token. Forward it only to the fixed configured CRM origin, never to an LLM, logs, storage or redirected origin. The CRM verifies the token, user, organization and permissions. Do not accept user/org identity from request-body fields.
Reuse existing service-auth protection separately. Never put a service secret in browser code. If the current authentication mechanisms conflict, stop and explain the exact blocker instead of weakening them. Keep this local/private; production delegated authentication is not settled by this task.

FLOW TO BUILD
1. Accept structured contact fields from a client and request a CRM proposal.
2. Return the CRM's needs_information response, including missing fields and normalized partial fields. The caller must be able to resubmit completed details without global/shared conversation state.
3. Return a valid proposal with its exact details, ID and payload hash. Do not create a contact or auto-confirm.
4. Accept a separate explicit confirmation for that same proposal and hash, then call the CRM confirmation operation.
5. Return the saved draft ID/status only from the CRM's successful response. A valid retry returns the original replayed result.
6. On timeout or lost response, report an uncertain outcome and preserve the same proposal for retry. Don't start a new proposal automatically. Map permission, validation, changed-payload, unknown-proposal and expiry failures honestly.

BOUNDARIES
Guide Q&A stays unchanged. Free-text AI field extraction, popup wiring, WhatsApp identity mapping and apartment-status actions are outside this slice. Don't label a structured API as completed conversational AI.

TEST AND REPORT
Test configuration, token forwarding/redirection safety, missing credentials, missing fields, proposals, confirmation, replay, CRM failures and uncertain-outcome retries. Verify tokens never reach logs or LLM payloads. Run the regression suite and report actual results, including failures.

Finish with changed files, exact new AI-service endpoint names, Swagger request/response examples, environment variable names and remaining gaps. No commit, no push, no deployment.