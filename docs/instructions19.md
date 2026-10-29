Do not push, commit to main or deploy. Make the changes, run the tests, then stop for review.
Assistant: follow the user's language (Hebrew or English) everywhere
Big picture
The assistant is a chat front door to the CRM. The user signs in with their CRM login and the assistant calls the CRM with that user's token. Later the same assistant logic will serve WhatsApp. Each user must be answered, in every reply and every screen, in the language that user last used. The language belongs to the CRM user: it is stored in the CRM as the user preference locale ("he" or "en", GET/PUT /api/preferences). The CRM value is the only stored copy; the assistant has no user table and may only hold the value in memory for the current session. At every new session the CRM value wins. A matching CRM task validates that value and keeps the preference contract stable.
What exists today
The page decides the language once from the browser (navigator.language) and sends language with each /assistant/turn. The CRM account's language is never read.
/chat (Ask) has no language input; the answer prompt says "write in the language of the question".
Knowledge search can return passages from both guides (about_english.md, about_hebrew.md), so answers mix languages.
Several texts are English only (placeholder find/update replies, add-contact pointer, parts of the page), and login errors show both languages in one line.
Changes
Read the language at login. After POST /auth/login succeeds (logged_in), the server calls the CRM GET /api/preferences with the new token, keeps only locale, and adds language ("he", "en" or null) to the login answer. Never pass other preference keys to the page. If the call fails or the value is not he/en, return null; login still succeeds. With null the page uses the browser language as a passive fallback and saves nothing. Only an explicit choice (toggle press, or the detection rule below) is ever written.
Save changes. New route POST /assistant/language, body {"language": "he" | "en"}, bearer token required, same login check and rate limit group as chat. It forwards PUT /api/preferences with {"key": "locale", "value": ...} and returns {"language": ...}. A failed save returns an error but never blocks a chat turn; the session keeps using the language, and the save is tried once more on the next turn that follows a language change. Last explicit write wins; another tab or channel that wrote meanwhile is picked up at the next login.
Page language is state. Replace the fixed language constant with a variable set from the login answer, the toggle or detection (below). Every user-visible page string comes from one he/en dictionary. Set <html lang>, dir (rtl for Hebrew) and bubble direction on every switch. A toggle button shows the other language name ("עברית" / "English"). Before login the language is unknown, so the login screen stays bilingual and has the toggle; a choice made there is saved after login (same rule as the CRM login screen).
Every server reply follows the language. Add optional language ("en"|"he") to the /chat request and always send it from the page. Audit every user-visible string the server produces (placeholder find/update replies, add-contact pointer, planner clarify text, gate replies, contact and apartment conversation texts, generation fallbacks) and give each a he and en version. Statuses and errors coming from the CRM are shown as the assistant's own he/en text chosen by status code; never show the CRM's raw English message.
Answers use the user's language, not the question's. Change the answer instruction to name the target language explicitly ("Write the answer in Hebrew" / "in English"). Keep proper nouns as written.
Retrieval stays across both guides. The user language fixes the language of the answer and the screen, not which guide is searched. Keep searching both guides (the model is multilingual) and translate the answer into the user's language. Optional idea, not part of this task and not to be built now: when two passages score close, prefer the one from the guide in the user's language. Report how many of the existing retrieval tests change, if any.
Apartment edit labels. Use label (Hebrew) or label_en (English) from editable_fields by language. If an option or label exists in only one language, show that one. Custom labels, option values, field values, IDs and keys are shown exactly as the CRM returns them; never translate or rewrite them. Only the assistant's own fixed texts are translated.
Last-used language: conservative rule, implement as written. This rule is a shared contract: a future WhatsApp client must apply the same rule and the same save call.
Only typed free text counts: an Ask question or a free message in a flow. Menu choices, button clicks, confirm words, commands and verbs that start a request (find, add, update, מצא, הוסף, עדכן), field values, names, phone numbers, emails, URLs, digits and punctuation are removed before counting and never change the language.
Count the remaining letters: Hebrew U+0590 to U+05FF, Latin A-Z a-z. A message is "clearly" Hebrew or English only if it has at least 4 letters and one script is at least 80 percent of them. Mixed, short, numeric-only or unclear messages leave the language unchanged.
If the language was never set in this session by a toggle press, one clear message in the other language switches it and saves it. If the user pressed the toggle in this session, two clear messages in a row in the other language are needed. A toggle press always applies at once, saves, and resets this counter.
Save only on an actual change, through POST /assistant/language. Never save the same value again. If the save fails, keep the language for the session, do not block the turn, retry once on the next turn after a change.
Race: the CRM holds the last explicit write. This session reads it only at login, so a switch made elsewhere is picked up at the next login.
The reply to a clearly detected message is already in the new language.
Privacy. Store only the two-letter value in the CRM. No message text, no detection scores, no new logging of message content.
Bilingual copy to add
Use
English
Hebrew
Toggle (shows other language)
English
עברית
Language saved
Language saved.
השפה נשמרה.
Save failed
I could not save your language. It will stay for this session.
לא הצלחתי לשמור את השפה. היא תישאר לשיחה הזו.

Existing he/en texts in the code stay as they are.
Out of scope
No WhatsApp integration, no phone-to-user matching, no CRM changes (separate task), no new database table, no change to the guides' content.
Tests (mocked CRM and model; add and run)
Login: CRM returns he, en, missing, invalid value, 500 on preferences: answer has language he, en, null, null, null and login still works. Other preference keys never appear in the answer.
POST /assistant/language: 401 without token; valid he/en forwards exactly one PUT with key locale; any other value returns 400 and calls nothing; CRM failure returns an error and no turn is blocked.
Detection: pure Hebrew, pure English, mixed 60/40, under 4 letters, digits only, commands only, a Hebrew name inside an English sentence, a URL or email inside a sentence: expected language each time; switch saves once, no save when unchanged; after a toggle press, one clear opposite message does not switch and two in a row do; a toggle press resets the counter.
Save fails once: turn still answered, language kept for the session, one retry on the next turn after a change, no repeated writes of the same value.
Menu clicks and field values (a Hebrew street name in an English session) never change the language.
Ask in each language still searches both guides and answers in the user language (translated when the best passage is in the other language); the answer prompt names the target language explicitly.
Custom labels and option values with only one language, and IDs, are returned to the page unchanged.
Every server string reachable through each intent and each gate status has both languages (test enumerates them, so a new string without both fails).
Page: html lang/dir, bubble direction and the dictionary switch with the toggle and after login; login screen bilingual; a choice made before login is saved after it. Run the page script check and report that it was not seen in a real browser if so.
Apartment edit shows label_en in English and label in Hebrew; one-language items show as they are.
Report back
Say what changed, what did not, the retrieval test results before and after (expected unchanged), and every string you could not give both languages.
