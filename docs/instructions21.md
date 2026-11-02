Understanding requests in free text - implementation spec (chat helper)
Do not push. Stop for review after each step. Local commits are fine.
The bigger picture
The chat helper (crm-ai-assitant) opens with one free text box. Today the first message goes straight to a fixed keyword parser: app/search_chat.py parse(), with the verb lists there and in app/planner.py (whose header says it is a stub that a model call will replace). A sentence that does not start with a known command word is searched whole, as a name or address. That is why "להוסיף תפילין לג'ון" was searched as one long name.
This task adds the understanding step we agreed on: the first message of a new request goes to the AI provider, which answers with a small fixed JSON. Code validates that JSON and starts the existing flows. The model proposes; code decides. Nothing is saved without the existing confirmation screens, and permissions do not change.
Done looks like this: the user types "להוסיף תפילין לג'ון" and the helper understands it is about the person ג'ון - it never offers to add a person named "תפילין לג'ון", under any circumstances, including an AI failure.
The other documents are unchanged: the MiniCRM API brief, the assistant instructions and the apartment contract stay as they are. This page replaces the earlier outline that was here with the buildable spec.
Reality to build around: the contact read/update endpoints and the visit route from fixes117 may not exist yet in the CRM this helper talks to. The understanding step must be useful before they land, and honest when an understood action is not available yet.
1. Where the AI call lives (design choice)
Inside the helper service, as a new module app/interpreter.py. It reuses the provider plumbing in app/generation.py: the key from GEMINI_API_KEY (environment only, never in code), ANSWER_PROVIDER, the model name from GEMINI_MODEL, the thinking level from GEMINI_THINKING_LEVEL. No new required environment variable and no new CRM endpoint.
It uses the configured provider and model deliberately, through that plumbing - never a hardcoded default that could silently call somewhere else. It builds its own short request: its own system instruction plus the user-written text. It never reuses the Ask flow's guide passages (RAG) or its answer prompt.
Why: the key already lives in the helper, understanding is a helper concern, and the CRM keeps owning data and permissions. The owner can move the call into the CRM later without changing the JSON contract in section 4.
2. When the AI runs
Only on the first message of a new request: the free text that today goes straight into parse(), with no carried conversation state. Plus one bounded exception: the clarification answer in section 6, which triggers at most one more AI call for the same request.
Never for anything else: button taps, numbered answers, field answers, yes/no confirmations, or any message inside an ongoing flow. Those stay deterministic, exactly as today, and never cause an AI call. No automatic retry after a failure.
3. What is sent to the provider
What the user typed, plus the interface language. Be honest about what that means: the typed text itself can be personal - a person's name, a note about a visit - and it is sent to the AI provider, the same way Ask questions already are today.
During a clarification, the only texts sent are the two the user wrote: the original message and the clarification answer, plus the language. Nothing else, ever: no login token, no CRM records, no search results, no guide passages. The key comes from the environment and is never logged, never written to files, never in tests.
4. The fixed JSON the model returns
The model answers with JSON only. This is the shape, in notation (string | null means a text value or null):
{
  "action": "search" | "create_contact" | "edit_contact" | "add_visit" | "edit_apartment" | "ask_question" | "unclear",
  "person": string | null,
  "field": string | null,
  "value": string | null,
  "date": string | null,
  "notes": string | null,
  "question": string | null,
  "ask": string | null
}
Two valid example answers. For "add tefillin to John during a visit today":
{"action": "add_visit", "person": "John", "field": "tefillin", "value": null, "date": "today", "notes": null, "question": null, "ask": null}
For a genuinely ambiguous message:
{"action": "unclear", "person": null, "field": null, "value": null, "date": null, "notes": null, "question": null, "ask": "האם לרשום ביקור אצל ג'ון, או לערוך שדה באיש הקשר?"}
action: what the user wants. "unclear" when more than one meaning fits, or when the model is not sure which person or field is meant.
person: the name or address words to search for, as the user wrote them.
field: the field the user named, in the user's own words (for example "תפילין"). Never a guessed CRM field name.
value: the new value for an edit, as the user wrote it.
date: a visit date as the user wrote it ("today", "יום ראשון").
notes: anything else the user said about a visit.
question: for ask_question, the user's question.
ask: for unclear, one short clarifying question in the user's language.
The system instruction tells the model: answer with this JSON and nothing else; copy the user's words into person, field, value, date and notes; never invent details the user did not say; when two readings are plausible, or the person or field is uncertain, answer "unclear" with a short ask.
5. Validation in code, and what happens on failure
A pydantic model validates the reply before anything uses it.
Not valid JSON, wrong types, or an action outside the list: the call counts as failed (rule below).
Extra keys are ignored. Every string is trimmed. Caps (design choice): person, field, date, question and ask up to 200 characters; value and notes up to 500. Anything longer makes the answer invalid, so it follows the failure rule - a long note is never silently cut.
person is required for create_contact, edit_contact and add_visit. If it is missing, treat the action as unclear with a generic ask for the name.
Failure rule: on any technical failure - timeout, provider error, missing key, or an invalid answer - the helper says honestly that it could not understand the message and asks the user to try again or phrase it differently. It never falls back to searching the original sentence, and never starts an add or a change from it. A new request the AI could not read goes nowhere except that message. This rule has no exceptions: no failure path may search the command words or offer a person named after them.
Follow-ups are untouched: numbered answers, field answers and yes/no confirmations run through the existing deterministic code with no AI call, whatever state the provider is in.
6. Routing after understanding
The helper acts only on a clear answer: the action is known and what to search for is known. A guessed name is never searched and never created. When the model is unsure - about the action, the person or the field - the helper asks first and searches nothing.
Before searching, the extracted person text is normalized for search only: leading search-noise words and prepositions are removed (for example the Hebrew "ל" prefix: "לג'ון" searches "ג'ון"). Normalization only removes; it never adds, reorders or changes the letters of a name. When removal is uncertain, the text is searched as written.
search: run the existing search with the normalized person (or the original text when person is null and the action is clearly a search).
create_contact: search the name first to catch an existing person; only then start the existing add-contact flow (proposals, then confirm).
edit_contact: search the person, then open with GET /api/assistant/contacts/{id} (fixes117 endpoint 2). If the CRM does not serve that route yet, say plainly that opening a person is not available yet and offer the CRM page link, exactly as today. Never pretend.
add_visit: the same opening path; the visit's fields come from the CRM (new_visit_fields in fixes117). Not served yet: the same honest message.
edit_apartment: the existing apartment flow - search, getapartment, field choices from the CRM.
ask_question: the existing Ask path.
unclear - the clarification state: the helper asks the "ask" question and keeps a small, bounded state holding only the two user-authored texts (the original message and, when it arrives, the answer). This state is not one of the deterministic follow-ups. The user's answer, together with the original message and the language, goes through one more AI interpretation. After that the state is cleared: a clear result routes normally; anything else (still unclear, or a failure) ends with an honest message inviting the user to phrase the request again from the start. There is never a third interpretation for the same request.
Worked two-turn example. Turn 1: "להוסיף תפילין לג'ון" - the model cannot tell a visit record from a contact-field change, answers unclear with "ask": "האם לרשום ביקור אצל ג'ון, או לערוך שדה באיש הקשר?". The helper asks exactly that and searches nothing. Turn 2: the user answers "ביקור" - the helper sends the original message, this answer and the language for one more interpretation; the model answers add_visit with person "ג'ון", field "תפילין"; the helper searches ג'ון and continues the visit flow.
Matching fields and values before any save: the interpreter never names CRM fields. Code matches the user's words against the field lists the CRM returns (editable_fields, new_visit_fields). Exactly one matching field is not enough to save: the value must also resolve to a value the CRM supports for that field - for an option field, exactly one of its listed options; for a text or date field, a value the CRM's validation accepts. An ambiguous or unsupported value is asked about, showing the real options. Every field the CRM marks required must be filled before the confirm screen. Nothing is auto-filled silently: if the user gave no visit date, the default (today) appears on the confirm screen like every other value. The confirm screen always shows every value that will be saved, and only an explicit yes saves. Field names like תפילין or מזוזה are never hardcoded in the helper - they come from the CRM.
7. Permissions and safety are unchanged
The CRM login check runs on every turn before anything else, as today. Role checks stay where they are: activists and campaign managers never see contacts, whatever the AI returns. Confirmation screens, one save per explicit yes, and request_id behavior are unchanged. The AI only chooses which existing flow starts.
8. Language
Every user-facing line stays in the user's CRM interface language, as today. JSON keys are fixed English. The model writes "ask" in the user's language.
9. Cost and limits
One short call per new request, plus at most one more for a clarification. Low thinking level. Timeout 10 seconds (design choice, the same style as the existing clients in app/generation.py and app/crm_api.py). Counted under the existing rate limiting.
10. Tests (provider mocked)
Validation: valid JSON for each action; an unknown action becomes a failure; extra keys are ignored; a missing person for a person action becomes unclear; a string over its cap becomes a failure, never a silent cut.
Failure behavior: timeout, provider error, missing key and invalid JSON each produce the honest try-again message. In every failure case: no search is run, no add or change starts, and no person is offered. The specific guard: with the input "להוסיף תפילין לג'ון" and a forced provider failure, the reply offers no person named "תפילין לג'ון" and runs no search.
Clarification state: a mocked unclear answer asks its question and performs no search and no create. The user's answer triggers exactly one more interpretation, and the second request contains only the original message, the answer and the language - no token, no CRM records, no search results. A second unclear answer (or a failure) ends with the honest restart message; there is no third call. The full two-turn example from section 6 runs end to end with mocks.
Normalization: "לג'ון" searches "ג'ון"; a name that starts with a real letter that looks like a preposition is searched as written.
Values: a value that matches no CRM option is asked about, never saved; an ambiguous option match is asked about; a missing required field is asked about before the confirm screen; a defaulted date appears on the confirm screen.
Understanding: with a mocked add_visit answer (person ג'ון, field תפילין), the helper searches ג'ון only - never the full sentence.
Routing: each clear action starts the right flow with the right search text.
Follow-ups (yes/no, numbers, field answers) never reach the provider.
A mocked edit_contact for an activist still gets no contact access.
The provider request contains only the user-written text or texts and the language - no token, no CRM records, no guide passages.
Hebrew and English examples for every action.
11. Out of scope
Building the fixes117 contact endpoints, the visit-route decision, any CRM change, any push or deploy. This task is the understanding step only.

