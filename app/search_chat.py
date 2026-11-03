"""The free-text start of the chat (docs/instructions20.md, sheepsidebay_crm fixes117).

One text box instead of a menu. Whatever the user types (a name, part of an
address, a phone number) goes to the CRM's combined search,
GET /api/assistant/search?q=, with the user's token. The answer is the count
first, then the list split into People and Apartments; each line carries its
id and is offered as a button that opens the record inside the chat.

A smart bot, not free AI: every step is a fixed path (search, list, open, edit,
add, confirm). Nothing here changes anything in the CRM; opening and editing
go to the flows (app/apartment_chat.py, app/person_chat.py,
app/contact_chat.py), which keep their own confirm-before-save rules.

Permissions: the CRM decides what each user may see. On top of that the chat
never shows people to a role other than admin or staff (an activist or a
campaign manager never gets contacts through the helper), and only those
roles are ever offered adding a person - and only when the CRM says
`can_add_person`. A role the chat doesn't know gets the narrowest treatment.

Understanding (docs/instructions21.md): the first message of a new request goes
to app/interpreter.py (one AI call), which names the action and the words to
search for. Code then starts an existing flow: search, add a person (the name is
searched first; the typed details go on into the add flow), open a person (edit
a field / record a visit), open an apartment, or answer a question. When the
model is unsure it asks one question; the answer gets one more AI call, never a
third. Any AI failure ends with "I couldn't understand" - the sentence is never
searched as a name and nothing is added from it.
Stateless on the server: the page holds `SearchState` (whether an add offer is
waiting for a yes, and the user's own words that must travel to the next turn:
the add request's details, or the request awaiting a clarification) and sends it
back. No token, no CRM records, no results in it.
"""

import re
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app import apartment_chat, contact_chat, crm_api, interpreter, person_chat
from app.apartment_chat import Apartment, full_address
from app.contact_chat import ConversationState, is_explicit_no, is_explicit_yes

Language = Literal["en", "he"]

# Roles that may see people through the helper (mirrors the CRM's own rule, fixes117).
PEOPLE_ROLES = ("admin", "staff")

# Normalizing the interpreter's search words (search only; removes, never adds or changes letters).
# Leading words that are never part of a name or an address.
NOISE_WORDS = {"to", "for", "at", "with", "the", "mr", "mrs", "ms", "את", "אצל", "עם", "של"}
# "to" written as one letter joined to the name ("לג'ון"). Whether it is the preposition or the
# name's first letter ("לאה") is uncertain, so the words are searched as written first, and
# without the letter only when that finds no one; a record found only that way is listed, never
# opened by itself (labelled design choice). Only ל: many names start with מ, ב or ו (משה, ברוך).
HEBREW_PREFIX = re.compile(r"^ל-?(?=\S{2})")
MAX_CARRIED_TEXT = 1000  # the user's own words held in the page state between turns


def search_texts(words: str) -> list[str]:
    """[as written (minus noise words), and maybe the same without a joined Hebrew preposition]."""
    parts = words.strip().strip(".,;:!").split()
    while len(parts) > 1 and parts[0].lower().strip(".,:") in NOISE_WORDS:
        parts = parts[1:]
    text = " ".join(parts)
    found = HEBREW_PREFIX.match(text)
    if found and text[1:2] == "-":
        return [text[found.end():]]  # "ל-ג'ון": the hyphen makes the preposition certain
    return [text, text[found.end():]] if found else [text]


class SearchState(BaseModel):
    """Held by the page between turns. No token, no CRM records, no search results."""

    model_config = ConfigDict(extra="forbid")

    # "Do you want to add a new person?" was asked: an explicit yes starts the add flow.
    offer_add: bool = False
    language: Language = "en"
    # The user's add request, carried into the add flow so its details are not typed twice.
    add_text: Optional[str] = Field(default=None, max_length=MAX_CARRIED_TEXT)
    # A request the interpreter found unclear: the next message is its clarification answer.
    clarify: Optional[str] = Field(default=None, max_length=MAX_CARRIED_TEXT)
    # The person the last request searched for: "create the contact" / "add a visit to him" next.
    last_person: Optional[str] = Field(default=None, max_length=200)


MESSAGES = {
    "en": {
        "too_short": "Please type a little more - at least 2 letters or digits.",
        "found": "I found {parts}.",
        "people": {1: "1 person"},
        "people_n": "{n} people",
        "apartments": {1: "1 apartment"},
        "apartments_n": "{n} apartments",
        "and": " and ",
        "people_title": "People:",
        "apartments_title": "Apartments:",
        "truncated": "(Only the first {count} are shown - type more to narrow it down.)",
        "no_match": "I found no matching person or apartment you can access for \"{query}\".",
        "no_match_add": "I found no matching person you can access for \"{query}\". "
                        "Do you want to add a new person?",
        "add_anyway": "Add a new person",
        "add_check": "Before adding \"{name}\", here is who already exists:",
        "add_start": "No one matching \"{name}\" exists yet, so let's add a new person.",
        "add_not_allowed": "Adding people isn't available for your role through the assistant.",
        "add_declined": "OK, nothing was added. Type a name, a phone number or part of an address to search.",
        "change_note": "You asked to change it to \"{value}\". Choose the field, then the value - "
                       "nothing is saved before you confirm.",
        "change_pick": "Choose the record you want to change:",
        "person_pick": "Choose the person:",
        "no_person": "I found no person you can access for \"{query}\".",
        "no_person_add": "I found no person you can access for \"{query}\". "
                         "Do you want to add \"{query}\" as a new person?",
        "visit_after_approval": "(A new person is saved as a draft; a visit can be recorded once the contact "
                                "is approved in the CRM.)",
        "no_apartment": "I found no apartment you can access for \"{query}\".",
        "too_long": "That's too long for one request - please keep it under {limit} characters.",
        "not_understood": "Sorry, I couldn't understand that request. Nothing was searched or changed. "
                          "Please try again, or say it a little differently.",
        "clarify_failed": "I still couldn't tell what you want to do. Nothing was searched or changed. "
                          "Please write the whole request again from the start.",
        "person_line": "{name}{address}{status} (#{id})",
        "no_name": "(no name)",
        "people_not_allowed": "People aren't available for your role through the assistant.",
        "yes": "Yes",
        "no": "No",
        "unauthorized": "Your CRM login is no longer valid. Please log in again.",
        "forbidden": "The CRM did not allow this search.",
        "search_missing": "Searching isn't available on this CRM yet (it needs the CRM's assistant search).",
        "crm_not_configured": "The assistant is not connected to a CRM (CRM_API_URL is not set).",
        "crm_unreachable": "I couldn't reach the CRM. Nothing was changed; send your message again to retry.",
        "crm_timeout": "The CRM didn't answer in time. Nothing was changed; send your message again to retry.",
        "crm_failed": "The CRM gave an answer I can't use ({status}). Nothing was changed.",
    },
    "he": {
        "too_short": "כתבו עוד קצת - לפחות 2 אותיות או ספרות.",
        "found": "מצאתי {parts}.",
        "people": {1: "אדם אחד"},
        "people_n": "{n} אנשים",
        "apartments": {1: "דירה אחת"},
        "apartments_n": "{n} דירות",
        "and": " ו",
        "people_title": "אנשים:",
        "apartments_title": "דירות:",
        "truncated": "(מוצגים רק {count} הראשונים - כתבו עוד כדי לצמצם.)",
        "no_match": "לא מצאתי אדם או דירה מתאימים שיש לכם גישה אליהם עבור \"{query}\".",
        "no_match_add": "לא מצאתי אדם מתאים שיש לכם גישה אליו עבור \"{query}\". "
                        "האם להוסיף אדם חדש?",
        "add_anyway": "הוספת אדם חדש",
        "add_check": "לפני שמוסיפים את \"{name}\", אלה מי שכבר קיימים:",
        "add_start": "אין עדיין מישהו שמתאים ל-\"{name}\", אז נוסיף אדם חדש.",
        "add_not_allowed": "הוספת אנשים אינה זמינה לתפקיד שלכם דרך העוזר.",
        "add_declined": "בסדר, לא נוסף דבר. כתבו שם, מספר טלפון או חלק מכתובת כדי לחפש.",
        "change_note": "ביקשתם לשנות ל-\"{value}\". בחרו את השדה ואז את הערך - "
                       "שום דבר לא נשמר לפני שתאשרו.",
        "change_pick": "בחרו את הרשומה שתרצו לשנות:",
        "person_pick": "בחרו את האדם:",
        "no_person": "לא מצאתי אדם שיש לכם גישה אליו עבור \"{query}\".",
        "no_person_add": "לא מצאתי אדם שיש לכם גישה אליו עבור \"{query}\". "
                         "האם להוסיף את \"{query}\" כאדם חדש?",
        "visit_after_approval": "(אדם חדש נשמר כטיוטה; אפשר לרשום ביקור אחרי שאיש הקשר יאושר ב-CRM.)",
        "no_apartment": "לא מצאתי דירה שיש לכם גישה אליה עבור \"{query}\".",
        "too_long": "זה ארוך מדי לבקשה אחת - כתבו בבקשה עד {limit} תווים.",
        "not_understood": "סליחה, לא הצלחתי להבין את הבקשה. לא חיפשתי ולא שיניתי דבר. "
                          "נסו שוב, או כתבו אותה קצת אחרת.",
        "clarify_failed": "עדיין לא הצלחתי להבין מה תרצו לעשות. לא חיפשתי ולא שיניתי דבר. "
                          "כתבו בבקשה את כל הבקשה מחדש.",
        "person_line": "{name}{address}{status} (#{id})",
        "no_name": "(ללא שם)",
        "people_not_allowed": "אנשים אינם זמינים לתפקיד שלכם דרך העוזר.",
        "yes": "כן",
        "no": "לא",
        "unauthorized": "ההתחברות ל-CRM אינה תקפה עוד. אנא התחברו מחדש.",
        "forbidden": "ה-CRM לא איפשר את החיפוש הזה.",
        "search_missing": "החיפוש עדיין אינו זמין ב-CRM הזה (נדרש החיפוש של העוזר ב-CRM).",
        "crm_not_configured": "העוזר אינו מחובר ל-CRM (CRM_API_URL לא הוגדר).",
        "crm_unreachable": "לא הצלחתי להגיע ל-CRM. לא שונה דבר; שלחו את ההודעה שוב כדי לנסות שוב.",
        "crm_timeout": "ה-CRM לא ענה בזמן. לא שונה דבר; שלחו את ההודעה שוב כדי לנסות שוב.",
        "crm_failed": "ה-CRM החזיר תשובה שאינני יכול להשתמש בה ({status}). לא שונה דבר.",
    },
}


def say(language: str, key: str, **values) -> str:
    return MESSAGES[language][key].format(**values)


def may_see_people(role: Optional[str]) -> bool:
    return role in PEOPLE_ROLES


# --- display -------------------------------------------------------------------

def person_name(lang: str, person: dict) -> str:
    name = " ".join(p for p in (person.get("name"), person.get("last_name")) if p and p.strip())
    return name or MESSAGES[lang]["no_name"]


def person_line(lang: str, person: dict) -> str:
    address = person.get("address")
    status = person.get("status_label")
    return say(lang, "person_line", name=person_name(lang, person), id=person["id"],
               address=f" - {address}" if address else "", status=f" [{status}]" if status else "")


def apartment_line(lang: str, apt: Apartment) -> str:
    """"address, apartment X, floor Y - resident": never the internal apartment id (fixes118)."""
    resident = apartment_chat.say(lang, "resident", name=apt.resident_name) if apt.resident_name else ""
    return f"{full_address(lang, apt)}{resident}"


def _count(lang: str, key: str, n: int) -> str:
    return MESSAGES[lang][key].get(n) or say(lang, key + "_n", n=n)


def count_line(lang: str, people: int, apartments: int) -> str:
    parts = []
    if people:
        parts.append(_count(lang, "people", people))
    if apartments:
        parts.append(_count(lang, "apartments", apartments))
    joiner = MESSAGES[lang]["and"]
    if lang == "he" and len(parts) == 2 and parts[1][0].isdigit():
        joiner = " ו-"  # "ו-3 דירות"
    return say(lang, "found", parts=joiner.join(parts))


def _answer(state: SearchState, outcome: str, reply: str, *, prompt: Optional[str] = None,
            choices=None, logout: bool = False) -> dict:
    """`reply` is the whole text (for any text channel); `prompt` the same without the lists,
    for the page, which shows `choices` as buttons instead. Each choice says what a tap does:
    open_person / open_apartment (with the id) or add_person / dismiss."""
    return {
        "intent": "search",
        "outcome": outcome,
        "reply": reply,
        "prompt": prompt if prompt is not None else reply,
        "choices": choices or [],
        "search_state": state.model_dump(),
        "logout": logout,
    }


def _add_choice(label: str, add_text: Optional[str]) -> dict:
    """Adding a person; `message` (the user's own request) goes on into the add flow,
    where the details are read from it, so nothing has to be typed twice."""
    choice = {"label": label, "action": "add_person"}
    if add_text:
        choice["message"] = add_text
    return choice


def _yes_no(lang: str, add_text: Optional[str]) -> list[dict]:
    return [_add_choice(say(lang, "yes"), add_text), {"label": say(lang, "no"), "action": "dismiss"}]


# --- the turn ------------------------------------------------------------------

def handle_turn(token: str, message: str, state: SearchState, role: Optional[str],
                ask: Callable[[str], dict]) -> dict:
    """A new request typed in the text box (or the answer to an add offer or to a
    clarifying question). `ask(question)` is the Ask path (guide answers, app/main.py)."""
    lang = state.language
    text = message.strip()

    if state.offer_add:
        add_text, state.offer_add, state.add_text = state.add_text, False, None
        if is_explicit_yes(text):
            return start_add(token, lang, role, add_text)
        if is_explicit_no(text):
            return _answer(state, "add_declined", say(lang, "add_declined"))
        # Anything else is a new request.

    if len(text) > MAX_CARRIED_TEXT:
        state.clarify = None
        return _answer(state, "too_long", say(lang, "too_long", limit=MAX_CARRIED_TEXT))

    if state.clarify is not None:
        # The answer to the clarifying question: one more interpretation, never a third.
        original, state.clarify = state.clarify, None
        result = interpreter.interpret([original, text], lang, state.last_person)
        if result.status != "ok" or result.understanding.action == "unclear":
            return _answer(state, "not_understood", say(lang, "clarify_failed"))
        return _route(token, lang, role, result.understanding, f"{original}\n{text}", ask, state.last_person)

    if len(text) < crm_api.MIN_SEARCH_LENGTH:
        return _answer(state, "too_short", say(lang, "too_short"))

    result = interpreter.interpret([text], lang, state.last_person)
    if result.status != "ok":
        # Never a fallback: the sentence is not searched and nothing is started from it.
        return _answer(state, "not_understood", say(lang, "not_understood"))
    understanding = result.understanding
    if understanding.action == "unclear":
        state.clarify = text
        return _answer(state, "clarify", understanding.ask)
    return _route(token, lang, role, understanding, text, ask, state.last_person)


def _route(token: str, lang: str, role: Optional[str], u: interpreter.Understanding, text: str,
           ask: Callable[[str], dict], last_person: Optional[str] = None) -> dict:
    """A clear understanding -> the existing flow. `text` is what the user wrote."""
    state = SearchState(language=lang, last_person=last_person)
    if u.action == "ask_question":
        return ask(u.question or text)
    if u.action in ("edit_contact", "add_visit") and not may_see_people(role):
        return people_refused(lang)  # before anything is searched
    if u.action == "create_contact" and not may_see_people(role):
        return start_add(token, lang, role, None)

    wanted = {"edit_apartment": "apartments", "search": "any"}.get(u.action, "people")
    body, query, as_written = _search(token, u.person or text, role, wanted)
    if body.get("status") == "too_short":
        return _answer(state, "too_short", say(lang, "too_short"))
    if body.get("status") != "ok":
        return _failure(state, body)
    people = body["people"]
    apartments = [Apartment(**a) for a in body["apartments"]]
    if u.action != "edit_apartment":
        state.last_person = query[:200]
    can_add = may_see_people(role) and body["can_add_person"] is True

    if u.action == "create_contact":
        if not can_add:
            return _answer(state, "add_not_allowed", say(lang, "add_not_allowed"))
        # The add flow reads the details from the user's words; a name remembered from the
        # previous request ("create the contact") isn't in them, so it goes first.
        add_text = text if u.person and u.person.lower() in text.lower() else f"{u.person}\n{text}"
        if not people:
            return _with_intro(start_add(token, lang, role, add_text), say(lang, "add_start", name=query))
        head = [say(lang, "add_check", name=query), count_line(lang, len(people), 0)]
        return _results(state, lang, head, people, [], body, add=_add_choice(say(lang, "add_anyway"), add_text))

    if u.action in ("edit_contact", "add_visit"):
        if not people:
            if not can_add:
                return _answer(state, "no_match", say(lang, "no_person", query=query))
            # Offer to add them with the name already known; a draft can't get a visit yet - said plainly.
            state.offer_add, state.add_text = True, query
            reply = say(lang, "no_person_add", query=query)
            if u.action == "add_visit":
                reply += "\n" + say(lang, "visit_after_approval")
            return _answer(state, "no_match", reply, choices=_yes_no(lang, query))
        if len(people) == 1 and as_written:
            # The user's own words go on: the flow matches them against the CRM's fields.
            answer = person_chat.open_person(
                token, lang, people[0]["id"], edit=u.action == "edit_contact", visit=u.action == "add_visit",
                field_words=u.field, value_words=u.value, date_words=u.date, notes=u.notes)
            return _with_intro(answer, _value_note(lang, u))
        head = [count_line(lang, len(people), 0), say(lang, "person_pick")]
        return _results(state, lang, head, people, [], body)

    if u.action == "edit_apartment":
        if not apartments:
            return _answer(state, "no_match", say(lang, "no_apartment", query=query))
        if len(apartments) == 1 and as_written:
            answer = apartment_chat.open_apartment(token, lang, apartments[0].lineage_id, u.field, u.value)
            return _with_intro(answer, _value_note(lang, u))
        head = [count_line(lang, 0, len(apartments)), say(lang, "change_pick")]
        return _results(state, lang, head, [], apartments, body)

    # search
    if not people and not apartments:
        if can_add:
            state.offer_add, state.add_text = True, query
            return _answer(state, "no_match", say(lang, "no_match_add", query=query), choices=_yes_no(lang, query))
        return _answer(state, "no_match", say(lang, "no_match", query=query))
    return _results(state, lang, [count_line(lang, len(people), len(apartments))], people, apartments, body)


def _search(token: str, words: str, role: Optional[str], wanted: str) -> tuple[dict, str, bool]:
    """The CRM search with the normalized words: as written first; without a joined Hebrew
    preposition only when that finds nothing of the wanted kind. People only for admin/staff.
    -> (answer, the words searched, whether they were the words as written)."""
    candidates = search_texts(words)
    body, query = {"status": "too_short"}, words
    for query in candidates:
        if len(query) < crm_api.MIN_SEARCH_LENGTH:
            continue
        body = crm_api.search_everything(token, query).body
        if body.get("status") != "ok":
            return body, query, query == candidates[0]
        body = {**body, "people": body["people"] if may_see_people(role) else []}
        hits = {"people": body["people"], "apartments": body["apartments"]}.get(
            wanted, body["people"] + body["apartments"])
        if hits:
            break
    return body, query, query == candidates[0]


def _results(state: SearchState, lang: str, head: list[str], people: list[dict], apartments: list[Apartment],
             body: dict, add: Optional[dict] = None) -> dict:
    lines, choices = [], []
    if people:
        lines.append(say(lang, "people_title"))
        for person in people:
            line = person_line(lang, person)
            lines.append("- " + line)
            choices.append({"label": line, "action": "open_person", "id": person["id"], "group": "people"})
        if body["people_truncated"]:
            lines.append(say(lang, "truncated", count=len(people)))
    if apartments:
        lines.append(say(lang, "apartments_title"))
        for apt in apartments:
            line = apartment_line(lang, apt)
            lines.append("- " + line)
            choices.append({"label": line, "action": "open_apartment", "id": apt.lineage_id,
                            "group": "apartments"})
        if body["apartments_truncated"]:
            lines.append(say(lang, "truncated", count=len(apartments)))
    if add:
        choices.append(add)
    prompt = "\n".join(head)
    return _answer(state, "results", prompt + "\n" + "\n".join(lines), prompt=prompt, choices=choices)


def _value_note(lang: str, u: interpreter.Understanding) -> Optional[str]:
    """A value without a named field can't be placed: it is mentioned, the user picks the field."""
    return say(lang, "change_note", value=u.value) if u.value and not u.field else None


def _with_intro(answer: dict, intro: Optional[str]) -> dict:
    if intro:
        answer["reply"] = intro + "\n\n" + answer["reply"]
        if answer.get("prompt"):
            answer["prompt"] = intro + "\n\n" + answer["prompt"]
    return answer


def start_add(token: str, lang: str, role: Optional[str], add_text: Optional[str]) -> dict:
    """The existing add-contact path (details, summary, save only after an explicit yes).
    With the user's own request, its details are read at once (the add flow's existing
    reading step) and the draft is proposed - nothing has to be typed twice.
    The CRM checks the permission again when the proposal is made."""
    if not may_see_people(role):
        return _answer(SearchState(language=lang), "add_not_allowed", say(lang, "add_not_allowed"))
    if not add_text:
        return contact_chat.start(lang)
    return contact_chat.handle_turn(token, add_text, ConversationState(language=lang))


def people_refused(lang: str) -> dict:
    """A role that may not see people asked to open one: refused before any CRM call."""
    return _answer(SearchState(language=lang), "forbidden", say(lang, "people_not_allowed"))


def _failure(state: SearchState, body: dict) -> dict:
    lang = state.language
    status = body.get("status")
    if status == "unauthorized":
        return _answer(SearchState(language=lang), "unauthorized", say(lang, "unauthorized"), logout=True)
    if status == "forbidden":
        return _answer(state, "forbidden", say(lang, "forbidden"))
    if status in ("search_missing", "crm_not_configured", "crm_unreachable", "crm_timeout"):
        return _answer(state, status, say(lang, status))
    return _answer(state, "crm_failed", say(lang, "crm_failed", status=status or "unknown"))
