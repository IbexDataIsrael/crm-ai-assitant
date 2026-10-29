Claude task: prepare the chat assistant for apartment editing (preparation only)
Rules first
Read and plan only. Do not implement against the apartment endpoints. Their final shape is not known yet.
Do not push. Do not commit. Do not change files in the CRM. Stop when done and wait for my review.
Do not call the live CRM. Do not call any AI provider.
Writing tests and mocks is allowed only as a proposal in your report. Do not save them into the project yet.
Big picture
The CRM is getting new endpoints so that a user can edit apartments from the assistant chat. The CRM decides which fields a user can edit. The assistant must never keep its own list of fields or options. The assistant only shows what the CRM sends and sends back the user's choice. The CRM work is being built now. When it is done, I will give you its real report. Your job now is to prepare, so the real work is fast and correct.
What the assistant should do (the intent, not a final contract)
Add a menu choice, "edit apartment", next to the existing create-contact choice.
Ask for the address and send it to the CRM. The CRM sends back the matching apartments. Each one is expected to include an apartment id, its address, and a list of the fields this user can edit.
Each editable field is expected to have a display text, a type, a current value, and, for fields with choices, a list of options (a list of several for fields that allow more than one).
If several apartments match, the user picks one. Then the user picks a field. If the field has options, the assistant shows them. Otherwise the user types the value.
Show an old value to new value confirm screen. After the user confirms, send the change.
Then show only what the CRM says it saved. If a request times out after sending, do not say saved or failed. Re-read from the CRM and report what it shows.
Every request uses the logged-in user's own CRM login, in the same way the create-contact flow does. There is no shared key. Treat the response shapes above as a proposal only. The real names and shapes come from the CRM's report. Do not invent them.
What to do now
Read the existing create-contact flow in the assistant, from the menu to the confirm step to the CRM call. Describe how it works: the conversation states, how it keeps the pending change, how it handles the confirm words, how it forwards the user's login to the CRM, and how it handles errors and timeouts.
Read how the assistant's tests mock the CRM today. Describe the pattern.
Find where each new piece would fit: the menu choice, the new conversation states, the routes that call the CRM, and the display of options. List the files and the functions you would touch. Do not touch them.
Look for anything in the current code that would stop a field list from being dynamic: any hardcoded field names, option lists or labels. Report each place.
Check how Hebrew text, right-to-left display and numbered choices are handled today, and what a list of many options (for example 20 or more) would need.
Propose a test plan, written as a list of cases with a short note of what each one proves. Cover: no match, one match, many matches, picking by number, a wrong number, a field with options, a field with several options, a field the user types, a field list that is empty, confirm and cancel, a double click while a request is in flight, a timeout after sending, an error from the CRM (not logged in, not allowed, rejected value, not found, conflict), and the CRM being unreachable.
Write down the questions that only the CRM's real report can answer.
Out of scope for now
Any code changes, commits or pushes.
Any calls to the live CRM or to an AI provider.
Putting the assistant inside the redesigned site, and WhatsApp. They come later.
When you finish
Tell me, in short plain English: how the create-contact flow works today, which files and functions the new flow would touch, the places that block a dynamic field list, your test plan, and the questions that wait for the CRM report. Do not push or commit. Wait for my review.



Claude task: update the chat assistant to the new apartment edit contract
Rules first
Don't push. Stop for my review when you finish.
Don't commit. Don't call the live CRM or any AI provider. Don't deploy.
Work only in this assistant project. Don't change the CRM.
Use mocked tests only.
If the code or a test shows something different from this brief, stop and tell me in plain words. Don't guess.
Why this task
The CRM side was changed. The old chat code was written for the first version of the apartment endpoints. That version only knew two fields and a separate fields list. The CRM now decides, for each user and each apartment, which fields can be edited. The chat must show only what the CRM returns. It must never keep its own list of fields, labels or options.
Status: the CRM change is built and its tests pass. I am pushing it now. Whether it is deployed is not confirmed yet. Treat the shapes below as the CRM's own report, not proven against a running CRM.
What changed in the CRM (the new contract)
All calls use the logged-in user's own login token. No shared key. Same as today.
GET /api/assistant/apartments/search?q=
Returns {results: [...], truncated: true or false}. At most 20 results. truncated is true when there were more.
Each result has: apartment_id (the current version id), lineage_id, building_address, apartment_name, floor, resident_name, editable_fields.
GET /api/assistant/apartments/by-lineage/{lineage_id}
Returns one result in the same shape as a search result. 404 if missing or not visible to this user. Use it to re-read after a timeout, a 404 or a 409.
POST /api/assistant/apartments/{apartment_id}/update with body {field_key, value}
The body uses field_key (the old body used field). For several options the value is a list.
Success returns: status ("updated" or "unchanged"), field_key, value (read back from the CRM), apartment_id (the NEW id after an update), previous_apartment_id (null when unchanged), lineage_id, apartment (a full result with fresh editable_fields).
"unchanged" means the value was already the same. No new version was created. Say so plainly. Do not say "saved".
value can be null in the answer when the saved value left the field not editable. Then show the apartment as returned and don't invent a value.
The old GET /api/assistant/apartments/fields is removed. Remove its call. Options now come inside editable_fields.
The editable_fields list
Each entry has: field_key, label (Hebrew text), label_en (English text), kind, current_value, and options (only for option fields).
kind is one of: single_option, multi_option, text, number, date, yes_no.
current_value is a list for multi_option. For other kinds it is the value as the CRM shows it.
Each option has: value, label (Hebrew), label_en. Send the option value back. Show the label.
Floor options are the building's floors. Report number options are the org's report numbers. Both come in the same option shape.
Show label when the chat is in Hebrew and label_en when it is in English. Use the same language choice the rest of the chat already uses.
Hidden fields, locked fields and internal fields are already left out by the CRM. Don't add them back, and don't filter by name. The set is different per user and per apartment. An activist gets fewer fields than an admin. Re-read the list for every apartment.
A field with no active options is not listed.
Errors to handle
403: the same message for every reason (hidden, locked, unknown field, apartment not assigned to this user). Say the field can't be edited here. Reload the apartment and its field list. Don't try to explain which reason.
422: the body is {status: "invalid", invalid_fields: [{field, code, value?}]}. Codes: expected_option, expected_list, value_not_allowed, expected_text, expected_number, expected_date, expected_yes_no, invalid_format. Show a short plain message that names the field, and ask for the value again. Nothing was written.
409: someone changed the apartment meanwhile. Re-read by lineage_id, show the current state, ask for the value again. Never resend on your own.
404: re-read by lineage_id. If that is also 404, say the apartment is no longer available.
401: log the user out, as today.
A lost answer (timeout, dropped connection, 5xx, unreadable 2xx): keep today's safety rule. The edit stays blocked, nothing is resent, and "yes" re-reads the lineage. Say "saved" only if the CRM shows the value that was sent.
What to change in the assistant
Remove the call to the fields endpoint and any code that depends on it. Take the field list from the apartment result (search or by-lineage or the update answer).
Rename the update body field from field to field_key. Use the new answer shape. Use the new apartment_id from the answer for the next step. Keep lineage_id as the stable identity.
Support all six kinds in the conversation, not only option fields:
single_option and multi_option: numbered list, as today.
yes_no: ask yes or no. First find the exact value to send in the CRM's rule file for apartment editing and its tests. Don't assume true or false or any other value. Ask me only if you cannot find it.
text: the user types the value. Send it as text.
number and date: the user types it. Send it as the CRM expects, and let the CRM check it. Don't build your own strict rules. Show the CRM's 422 message if it refuses.
Clearing a value: first find in the same CRM rule file and tests which kinds can be emptied and how. Don't assume a clearing rule or a wire value. Ask me only if you cannot find it.
Confirm screen: show the old value and the new value using current_value and labels. After the user confirms, send. After the answer, show only what the CRM returned.
Stay stateless as today. Keep the login token and the field list out of the page state.
Keep the menu choice, the address search step, the "only the first 20" note, and the Hebrew and right-to-left handling.
Long option lists: floors and report numbers can be long. Make sure numbered lists of 20 or more options work and stay readable.
Tests (mocked CRM only)
Update the existing apartment chat tests to the new shapes. Add cases for:
no match, one match, many matches, truncated note
an activist result with fewer fields than an admin result for the same apartment
each kind: single_option, multi_option, text, number, date, yes_no
a 403 with the same message for different reasons
a 422 with each code, and the message names the field
a 409 then re-read then ask again, with nothing resent
"unchanged" shown as unchanged
a lost answer stays blocked and nothing is resent
value null in the answer
a search result with an empty editable_fields list: say nothing can be edited for this apartment
a check that no field name or option list is kept in the assistant code
Do one mutation check: make a lost answer count as "not sent" and confirm the tests fail. Then put it back.
Not part of this task
No change to the CRM. No deploy. No live CRM or provider calls.
The CRM's normal dashboard save does not yet refuse hidden or locked fields. I haven't decided yet. Don't work around it here.
Staff and campaign manager users follow the admin rules in the CRM for now. That may change. Don't add role logic in the assistant. Use only what the CRM returns.
When you finish
Tell me, in short plain English: what you changed and in which files, what you could not check without the real CRM, anything unclear in the CRM's shape, the test numbers, and what you want me to decide. Then stop and wait for my review.
