"""The free-text start of the chat (docs/instructions20.md, sheepsidebay_crm fixes117).

One text box instead of a menu. Whatever the user types (a name, part of an
address, a phone number) goes to the CRM's combined search,
GET /api/assistant/search?q=, with the user's token. The answer is the count
first, then the list split into People and Apartments; each line carries its
id and is offered as a button that opens the record inside the chat.

A smart bot, not free AI: every step is a fixed path (search, list, open, edit,
add, confirm). Nothing here changes anything in the CRM; opening and editing
go to the existing flows (app/apartment_chat.py, app/contact_chat.py), which
keep their own confirm-before-save rules.

Permissions: the CRM decides what each user may see. On top of that the chat
never shows people to a role other than admin or staff (an activist or a
campaign manager never gets contacts through the helper), and only those
roles are ever offered adding a person - and only when the CRM says
`can_add_person`. A role the chat doesn't know gets the narrowest treatment.

Short request forms (phase 1, a labelled design choice): "add <name>" first
searches the name to catch someone who already exists, then offers adding;
"change <record> to <value>" searches the record part and, when exactly one
apartment matches, opens it. A leading "find" / "search" is dropped. Anything
else is a plain search. Wider sentence understanding is part 2 (an AI step,
not built).

Stateless on the server: the page holds `SearchState` (only whether an add
offer is waiting for a yes) and sends it back. No token, no results in it.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from app import apartment_chat, contact_chat, crm_api, planner
from app.apartment_chat import Apartment, full_address
from app.contact_chat import is_explicit_no, is_explicit_yes

Language = Literal["en", "he"]

# Roles that may see people through the helper (mirrors the CRM's own rule, fixes117).
PEOPLE_ROLES = ("admin", "staff")

CHANGE_VERBS = {"change", "set", "update", "שנה", "שני", "שנו", "תשנה", "תשני", "תשנו",
                *planner.ACTION_VERBS["update"]}
ADD_VERBS = set(planner.ACTION_VERBS["add"])
FIND_VERBS = set(planner.ACTION_VERBS["find"])
# What may follow "add" before the name: "add a contact Yosef Cohen", "הוסף איש קשר יוסף כהן".
ADD_NOUNS = re.compile(
    r"^(?:(?:a\s+)?(?:new\s+)?(?:contact|person)|איש\s+קשר(?:\s+חדש)?|אדם(?:\s+חדש)?)(?:\s*[:,-]\s*|\s+|$)",
    re.IGNORECASE)
CHANGE_TO_EN = re.compile(r"^(?P<record>.+?)\s+to\s+(?P<value>\S.*)$", re.IGNORECASE)
CHANGE_TO_HE = re.compile(r"^(?P<record>.+)\s+ל-?(?P<value>\S.*)$")
LEADING_WORD = re.compile(r"\s*(\w+)")


class SearchState(BaseModel):
    """Held by the page between turns. No token, no search results."""

    model_config = ConfigDict(extra="forbid")

    # "Do you want to add a new person?" was asked: an explicit yes starts the add flow.
    offer_add: bool = False
    language: Language = "en"


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
        "person_line": "{name}{address}{status} (#{id})",
        "no_name": "(no name)",
        "open_person": "Opening a person inside the chat isn't available yet - it needs a CRM update.",
        "open_person_link": "Open this person's page in the CRM:",
        "open_person_no_link": "The CRM web address isn't configured here (CRM_WEB_URL), so I can't link to the page.",
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
        "person_line": "{name}{address}{status} (#{id})",
        "no_name": "(ללא שם)",
        "open_person": "פתיחת אדם בתוך הצ'אט עדיין אינה זמינה - היא דורשת עדכון ב-CRM.",
        "open_person_link": "פתיחת הדף של האדם ב-CRM:",
        "open_person_no_link": "כתובת האתר של ה-CRM לא הוגדרה כאן (CRM_WEB_URL), ולכן אין קישור לדף.",
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


# --- reading the request -------------------------------------------------------

def parse(message: str) -> tuple[str, str, Optional[str]]:
    """(kind, text to search, wanted value). kind: "search", "add" or "change"."""
    text = message.strip()
    rest = text
    while True:
        match = LEADING_WORD.match(rest)
        if not match or match.group(1).lower() not in planner.POLITE_WORDS:
            break
        rest = rest[match.end():].lstrip(" ,")
    match = LEADING_WORD.match(rest)
    verb = match.group(1).lower() if match else ""
    after = rest[match.end():].strip() if match else ""
    if text.endswith("?"):
        return "search", text, None
    if verb in ADD_VERBS:
        return "add", ADD_NOUNS.sub("", after, count=1).strip(), None
    if not after:
        return "search", text, None
    if verb in FIND_VERBS:
        return "search", after, None
    if verb in CHANGE_VERBS:
        found = CHANGE_TO_EN.match(after) or CHANGE_TO_HE.match(after)
        if found:
            return "change", found.group("record").strip(), found.group("value").strip()
        return "search", after, None
    return "search", text, None


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
    resident = apartment_chat.say(lang, "resident", name=apt.resident_name) if apt.resident_name else ""
    return f"{full_address(lang, apt)}{resident} (#{apt.apartment_id})"


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


def _yes_no(lang: str) -> list[dict]:
    return [{"label": say(lang, "yes"), "action": "add_person"},
            {"label": say(lang, "no"), "action": "dismiss"}]


# --- the turn ------------------------------------------------------------------

def handle_turn(token: str, message: str, state: SearchState, role: Optional[str]) -> dict:
    lang = state.language
    text = message.strip()

    if state.offer_add:
        state.offer_add = False
        if is_explicit_yes(text):
            return start_add(lang, role)
        if is_explicit_no(text):
            return _answer(state, "add_declined", say(lang, "add_declined"))
        # Anything else is a new search.

    kind, query, value = parse(text)
    if kind == "add" and (not query or not may_see_people(role)):
        # "add a contact" with no name: nothing to search for. A role that may not add:
        # refused at once, before anything is searched.
        return start_add(lang, role)
    if len(query) < crm_api.MIN_SEARCH_LENGTH:
        return _answer(state, "too_short", say(lang, "too_short"))

    result = crm_api.search_everything(token, query)
    body = result.body
    if body.get("status") != "ok":
        return _failure(state, body)

    people_allowed = may_see_people(role)
    people = body["people"] if people_allowed else []
    apartments = [Apartment(**a) for a in body["apartments"]]
    can_add = people_allowed and body["can_add_person"]

    if kind == "change" and len(apartments) == 1 and not people:
        answer = apartment_chat.open_apartment(token, lang, apartments[0].lineage_id)
        note = say(lang, "change_note", value=value)
        answer["reply"] = note + "\n\n" + answer["reply"]
        answer["prompt"] = note + "\n\n" + answer["prompt"]
        return answer

    if not people and not apartments:
        if kind == "add" and can_add:
            answer = start_add(lang, role)
            intro = say(lang, "add_start", name=query)
            answer["reply"] = intro + "\n" + answer["reply"]
            return answer
        if can_add:
            state.offer_add = True
            return _answer(state, "no_match", say(lang, "no_match_add", query=query), choices=_yes_no(lang))
        return _answer(state, "no_match", say(lang, "no_match", query=query))

    head = [say(lang, "add_check", name=query)] if kind == "add" else []
    head.append(count_line(lang, len(people), len(apartments)))
    if kind == "change":
        head.append(say(lang, "change_pick"))
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
    if kind == "add" and can_add:
        choices.append({"label": say(lang, "add_anyway"), "action": "add_person"})
    prompt = "\n".join(head)
    return _answer(state, "results", prompt + "\n" + "\n".join(lines), prompt=prompt, choices=choices)


def start_add(lang: str, role: Optional[str]) -> dict:
    """The existing add-contact path (details, summary, save only after an explicit yes).
    The CRM checks the permission again when the proposal is made."""
    if not may_see_people(role):
        return _answer(SearchState(language=lang), "add_not_allowed", say(lang, "add_not_allowed"))
    return contact_chat.start(lang)


def open_person(lang: str, contact_id: int, role: Optional[str]) -> dict:
    """No CRM contact endpoint for the helper yet (owner: wait): say so, and link to the
    person's full page in the CRM web app, which checks the permission itself."""
    state = SearchState(language=lang)
    if not may_see_people(role):
        return _answer(state, "forbidden", say(lang, "add_not_allowed"))
    url = crm_api.contact_page_url(contact_id)
    reply = say(lang, "open_person")
    if url:
        label = say(lang, "open_person_link")
        answer = _answer(state, "not_available", reply + "\n" + label + " " + url, prompt=reply)
        answer["link"] = {"label": label, "url": url}
        return answer
    return _answer(state, "not_available", reply + "\n" + say(lang, "open_person_no_link"))


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
