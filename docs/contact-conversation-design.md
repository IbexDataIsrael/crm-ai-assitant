# Login + add-contact conversation: design choices (25 October 2026)

For docs/instructions15_the_whole_picture.md. Built and tested with a mocked CRM and mocked model only.

## Login contract (read from sheepsidebay_crm backend/app/routers/auth.py, read only)

`POST /api/auth/token`, form-encoded `username` (username or email), `password`, optional `organization_id` (int).
Answer `LoginResponse`: `access_token`, `token_type`, `requires_org_selection`, `organizations` [{id, name, logo_url}].
Errors: 401 `auth.error.invalid_credentials`; 403 `auth.error.not_verified` / `org_pending` / `org_disabled` (detail {code, message}).

## Choices

1. **Login proxy** `POST /auth/login` (JSON `{username, password, organization_id?}`) -> CRM form post, redirects not followed. The body is parsed by hand so a malformed request is refused (422) without echoing it (FastAPI's default validation errors echo the input, password included). Answer `Cache-Control: no-store`, no cookie.
2. **Multi-organization**: the CRM needs the password again with `organization_id`, so the password stays in its form field until the user picks an organization, then is cleared. It is never put anywhere else.
3. **One endpoint for the conversation**: `POST /assistant/turn` `{intent, message, state?, language?}`. The intent comes only from the menu buttons. `update_contact` answers "not available yet" and calls nothing. `ask_question` runs the unchanged `/chat` logic. `create_contact` needs the bearer token.
4. **Stateless server, state in the page**: the page holds `state` = {fields, asked, pending proposal, restart_offered, language} in memory; it contains no token (extra keys are refused). The server keeps nothing between turns, so nothing can leak between logins. The page drops the token and the state on logout, new login and any 401.
5. **Confirm only on an explicit yes**: a fixed word list (`YES_WORDS` in app/contact_chat.py: "yes", "confirm", "כן", "אישור", ...), matched on the whole message after removing punctuation. Never the model. "yes but change the phone" is not a yes. Confirm sends the pending proposal's id, hash and its exact fields (so the CRM also rejects a mismatch). If the held fields differ from the pending proposal's fields, the proposal is stale: it is dropped and a new one is made, never confirmed.
6. **The model reads fields only**: it gets one message plus the list of fields asked for - not the token, not the history, not the fields collected so far. JSON reply parsed strictly; unknown keys (e.g. organization_id) ignored. Gemini's `response_format` is not used, because its exact shape is not verified live.
7. **No key / model failure**: the conversation asks one field at a time and takes the answer as that field. So the flow works with no AI provider at all.
8. **All reply wording is fixed text** (English/Hebrew), not model-written, so the shown draft is exactly the CRM's draft. Language: Hebrew letters -> Hebrew; Latin letters -> English, except a bare answer to a single asked field (a name, phone or email) keeps the current language.
9. **A refused optional field** (phone/email/address) can be left empty by replying "skip" / "דלג". The refused value is removed from the held fields so it is not resubmitted unchanged.
10. **Typed "add a contact" in /chat** no longer shows demo data; it points to the menu. Find / update demos are unchanged.

## Wording for each code (English; Hebrew in MESSAGES in app/contact_chat.py)

| Code | Shown |
| --- | --- |
| needs_information | "Please tell me the {fields}." |
| invalid_phone | The phone number "{value}" isn't valid. |
| phone_taken | Another contact already has the phone number "{value}". |
| invalid_email | The email "{value}" isn't a valid address. |
| too_long | The {field} is too long (at most {max_length} characters). |
| other field code | The CRM did not accept the {field} ({code}). |
| proposal | Here is the exact draft that will be saved: ... Reply "yes" / "no" / what to change. |
| created | Saved as a draft contact (#id): {fields read back by the CRM} It is waiting for approval in the CRM. |
| created, replayed | Already saved by the earlier confirmation - the same contact (#id), no duplicate was created. |
| uncertain | The CRM's answer was lost, so I don't know whether the draft was saved. Reply "yes" to check safely with the same proposal. (No new proposal until settled.) |
| crm_unreachable (confirm) | Couldn't reach the CRM, so the confirmation was not sent and nothing was saved. Reply "yes" to try again. |
| conflict (409, retryable) | The CRM couldn't confirm this right now; nothing new was saved. Reply "yes" to try again with the same draft. |
| payload_mismatch (409) | The details don't match the proposal, so nothing was saved. A fresh proposal is shown (needs a new yes). |
| expired (410) | The proposal expired before it was confirmed, so nothing was saved. Reply "yes" for a new proposal (a proposal only, never a confirm). |
| unknown_proposal (404) | The CRM has no such proposal for this login, so nothing was saved. Same new-proposal offer. |
| unauthorized (401) | Your CRM login is no longer valid. Please log in again. (Logs out, clears all state.) |
| forbidden (403) | The CRM does not allow your user to create contacts. Nothing was saved. |
| crm_not_configured | The assistant is not connected to a CRM (CRM_API_URL is not set). Nothing was sent. |
| crm_unreachable / crm_timeout (proposal) | Couldn't reach the CRM / didn't answer in time. Nothing was saved; send again. |
| anything else | The CRM gave an answer I can't use ({status}). Nothing was saved by this step / not confirmed. |
| Login: invalid_credentials, not_verified, org_pending, org_disabled, rate_limited, ... | LOGIN_ERRORS in static/index.html (English / Hebrew). |

## Known limits (not fixed here)

- A tampered page could replay an old, still-valid proposal and confirm it: proposals can't be cancelled (no CRM endpoint) and the server keeps no state. The CRM still checks user, hash and fields; the user could do the same with their own token directly against the bridge.
- What the user types for a contact (names, phones, emails) goes to the answer provider for field reading. On Gemini's free tier Google may use it to improve its products.
- Logout drops the token from the page only; the JWT stays valid at the CRM until it expires.
- Not checked in a real browser yet.
