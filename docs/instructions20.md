Chat helper - free-text search and edit through the CRM API
Do not commit or push. Stop for review after each step.
The big picture
Today the chat opens with a quick menu (Create / Update / Ask). The next step replaces that start with one free text box. There is no menu of actions and no "What do you want to do?" question. The user types a name, part of an address, or a short request like "add Yosef Cohen" or "change 2C to English".
This is a smart bot, not free AI: every step follows a fixed, known path - search, list, open, edit, add, confirm. The bot never invents actions, and nothing is ever saved without the user seeing it and confirming.
Permissions: activists never get contact results or contact details through the helper, and a campaign manager never gets contacts either. Note for review: the CRM's main search today is more permissive for activists (coverage-scoped contacts under the organization's mekurav opt-in). That existing discrepancy is flagged for the owner's review - nothing here changes existing permissions.
Prerequisites
The endpoints in this document exist, or are being added, on the CRM's main branch only - other branches may not have them. Before building a flow that consumes an endpoint, confirm the CRM this assistant points at actually serves that route with this exact contract.
Nothing in this document authorizes a push, a merge or a deploy.
The assistant has no login or data of its own: it uses the CRM's login token and the CRM's data, from CRM_API_URL. Test and production stay isolated - a test assistant points at a test CRM address, never at production data or production logins.
The CRM endpoints the assistant uses
Existing - keep as they are:
POST /api/auth/token - login. GET /api/auth/me - the token check already required on every turn.
GET /api/preferences - reads the user's current interface language (locale). The assistant never changes the interface language; there is no language setting in the chat.
POST /api/assistant/apartments/{apartment_id}/update - the apartment update, unchanged.
POST /api/assistant/proposals and POST /api/assistant/proposals/{proposal_id}/confirm - the add-contact draft flow, unchanged.
New - being added in the CRM; the names and fields here must match that build exactly:
GET /api/assistant/search?q= answers {people: [{id, name, last_name, phone, address}], apartments: [same objects as the apartment search, with editable_fields], people_truncated, apartments_truncated, can_add_person}.
GET /api/assistant/apartments/getapartment?id=<lineage id> - opens and re-reads the current version of an apartment, in the same object shape as the apartment search (including editable_fields). Called after the user picks an apartment from the combined search, and for every re-read.
GET /api/assistant/contacts/{contact_id} answers the contact's details plus editable_fields: [{field_key, label, kind, current_value, options?}].
POST /api/assistant/contacts/{contact_id}/update with {field_key, value} answers {status: updated | unchanged, field_key, value read back, contact}.
Adding a visit to a contact: the CRM has POST /api/contacts/{contact_id}/visits today; the assistant's exact route and field discovery are being settled with the owner - see Adding a visit below.
The free-text turn, step by step
One free text box. An empty line or a single letter is not searched (the CRM needs at least 2 characters) - the bot asks for a little more text.
The text goes to GET /api/assistant/search?q= with the user's token.
The bot answers count first, conversationally, in the user's language: "I found 2 people with this name" / "מצאתי 2 אנשים בשם הזה". Then the list, split into People and Apartments. Each line has enough to tell records apart (a person: name and address; an apartment: address and building) and carries its id. When a truncated flag is set, add the "only the first 20" note.
The user picks a result, and it opens inside the chat - nothing requires leaving the chat:
Person: GET /api/assistant/contacts/{id}, show the details, and offer the actions that fit this record, starting with Edit and Add a visit (see Adding a visit). Edit works like the apartment flow: the user picks a field from editable_fields (numbered lists for choices, like the apartment kinds), gives the new value, sees a confirm screen (from the old value to the new one), and only an explicit yes sends the update. The reply shows only what the CRM returned. A direct link to the person's full page in the CRM is always shown too.
Apartment: open it with GET /api/assistant/apartments/getapartment?id=<its lineage id>; the answer carries the apartment's current values, labels and allowed answers. Only status fields are editable - the ones the CRM has configured at that moment, never a hardcoded list; the address and the apartment number are never editable through the helper, and the CRM enforces that on the server, not just in the bot. The full apartment flow - choose, edit, confirm, save, read back - is specified end to end in the separate apartment document: https://docs.google.com/document/d/1JflhB-YLj6HULcvDBwNV4YVd7IKV5y4jcuFJROyBrW0/edit
No results: an activist is never offered adding (see the permissions rule above). For other roles, if can_add_person is true, ask "I found no matching person you can access. Do you want to add a new person?" A No goes back to the text box. A Yes starts the existing add-contact path: the details one at a time, a summary, and saving only after the user confirms. If the user stops answering, nothing is saved. If can_add_person is false, say plainly that there are no matches and stop - do not offer adding. The user's role comes from the CRM's login check (GET /api/auth/me), which already runs on every turn.
Short request forms (phase 1): "add <name>" and "change <apartment> to <value>" are recognized, but the rules above still hold - an add request first runs the name search to catch a person who already exists, and an edit request first finds the record this user may see. Only then does the add or edit path start. Any other text falls back to a plain search. (Labelled design choice to confirm: in phase 1 the bot understands these short forms; wider sentence understanding can come later.)
Delete is never offered, anywhere.
Adding a visit to a person
A visit is a dated log entry on a person. The actions offered on an opened person include Add a visit, next to Edit. The same permission rule as every other contact feature applies: a role that cannot see contacts through the helper (an activist, a campaign manager) never sees a person and never gets this action.
What the CRM knows about a visit today: the date (required), the visit type (an option list), the location, notes, interactions (each one a category from an option list, plus a note), and a Torah thought (an option list). The organization can also define its own extra visit fields. The field list, the labels and the allowed answers all come from the CRM, in the user's current interface language - the bot never hardcodes field names or option lists.
Collecting: one question at a time. An option field is a numbered list built from the CRM's current options; a text field takes the user's words as-is. The date defaults to today unless the user gives another one. Every field except the date is optional - the user can skip it.
Confirm screen, always: the whole visit as it will be saved, every collected field. Only an explicit yes sends the save; a no cancels with nothing saved.
After the CRM answers, show only what the CRM returned: the saved visit, with its date and the person it belongs to. Then offer the next step: back to the person, add another visit, or back to search.
Safety: the same rules as every other flow. One save per explicit yes, nothing resent automatically. 403 is the one fixed message. A 404 means the person no longer exists - say so plainly. A 422 shows the CRM's plain reason. A lost answer is BLOCKED: on the user's next yes the bot re-reads the person's visit list and reports what the CRM shows - it never claims the save on its own.
Endpoint (with the owner): the CRM already has POST /api/contacts/{contact_id}/visits (the date is required; every other field is optional; a campaign manager is refused). The owner is handling the MiniCRM side, so whether the assistant calls that route directly or a new /api/assistant route with helper-specific permissions and a visit field-discovery contract is the owner's decision. Until he settles it, treat the endpoint and the field-discovery details as open. (Labelled open choice: with the owner.)
Direct links to CRM pages
Every opened record also shows a direct link to its full page in the CRM web app. A person link is /dashboard/contacts/{id}. An apartment link goes by the apartment's stable lineage id, so a link sent earlier keeps working after later edits; the stable route is being added on the CRM side (with the owner) - see the apartment document. The web app's address comes from a new environment value (for example CRM_WEB_URL), separate from CRM_API_URL.
Safety rules - the same standard as the apartment flow
One save per explicit yes. Nothing is resent automatically - the only exception is the bounded same-request_id retry described in the lost-answer rule.
401: log out. 403: one fixed message for every reason, then reload. 409 or 404: re-read the record and ask again.
A lost answer (timeout, dropped connection, 5xx, an unreadable response) means BLOCKED: nothing is resent, and on the user's next "yes" the bot re-reads and reports the change as unconfirmed, unless the CRM proves the original write. Exception, once the CRM supports it (see the apartment document): an update sent with a request_id may be retried with the same request_id and an identical body - the CRM returns the original result and never writes twice, and only that answer lets the bot say saved. A replayed success confirms the save as of that attempt, not the current state. The same pattern is meant for contact updates once the contact-edit rules are settled; visit creation is not covered.
Every reply follows the user's current CRM interface language. CRM values, labels and ids stay exactly as the CRM sent them.
The assistant never writes field names, labels or option lists of its own - they all come from the CRM.


part 2

Understanding requests in free text - chat helper addition
Do not commit or push these changes. This page is a work plan only.

The bigger picture
The chat helper talks with users in a chat. Today, the first message of a request is treated as a search: a name or a street. This addition lets the user write the request in natural language, in Hebrew or English. AI reads the first message and says what is being asked. The existing code still decides what happens and does the work.
1. The first message of a new request goes to AI
Example: "I visited David Cohen today and we spoke about mezuzahs."
AI answers with a small fixed JSON: the request (search, create a contact, edit an opened contact, add a visit, or change an apartment status field) and the details it found (name, date, field, value).
The answer always has this fixed JSON shape. Code validates it before using it, like the existing natural-language query feature (/api/ai/query): the model proposes, code decides.
2. Follow-up messages do not go to AI
Numbered answers, field answers and yes/no confirmations stay in the existing deterministic flow. Only the first message of a new request is interpreted.
3. No word-count rule
A short text can be a command ("צור איש קשר" - create a contact) and a long text can be a plain search (a full address). Length says nothing about meaning, so every new request is interpreted in the same way.
4. After understanding, the existing flow runs unchanged
Code finds the contact or apartment, checks permissions, asks one question at a time for anything missing, and shows the confirmation screen before any save.
AI cannot skip confirmation, cannot invent fields, and cannot change permissions. Activists still never see contact results or details; campaign managers still never get contacts.
5. When understanding fails, the helper asks
If the AI call fails, times out, or returns something that is not a valid known request, the helper asks the user what they meant. It never guesses a change and never runs a guessed edit or save.
6. Safety rules stay exactly as they are
Confirmation before save, permission checks on every call, and request_id on updates are unchanged. AI only chooses which existing flow to start.
Open choice (with the owner)
Where the AI call lives: inside the helper service, or as a small CRM endpoint next to the existing natural-language query. The JSON shape and the safety rules are the same either way.

The other documents are unchanged: the API brief, the assistant instructions and the apartment contract stay as they are. This page only adds the understanding step.

