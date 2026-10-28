"""The edit-an-apartment conversation (sheepsidebay_crm docs/fixes114.md).

Stateless on the server, like the add-contact conversation
(app/contact_chat.py): the page holds the state and sends it with every turn;
the token comes separately and goes only to the bridge (app/crm_api.py).

The order the owner asked for:
1. "Edit an apartment" in the menu.
2. The chat asks for the address.
3. The address goes to the CRM, which answers with the full address and the
   apartment id (several matches -> the user picks one).
4. The chat offers the fields this user may edit - the CRM's list, which the
   admin controls (activist edit locks), never a list kept here.
5. The user picks a field, then a value from the CRM's options. The chat sends
   apartment id + field + value.
6. The reply shows what the CRM saved, from the CRM's answer - not from what
   was sent.

Safety rules:
- One update per user action. The page disables input while a turn is in
  flight, and nothing here ever resends an update by itself.
- 409 (changed meanwhile) or 404: re-read the apartment by its lineage id
  (its id changes on every edit, the lineage id never does), show the current
  state, and ask for the value again. Never resend automatically.
- A lost answer (timeout, dropped connection, CRM 5xx): the edit is BLOCKED.
  Nothing is resent; "yes" re-reads the lineage. If the CRM shows the value
  that was sent, it was saved; otherwise the user is told it is not known and
  can check again or stop. (The CRM has no operation-status route yet that
  could settle it for sure.)
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app import crm_api
from app.contact_chat import detect_language, is_explicit_no, is_explicit_yes

Language = Literal["en", "he"]
Step = Literal["address", "choose_apartment", "choose_field", "choose_value", "blocked"]

# Clears a multi-value field (language): no option selected.
CLEAR_WORDS = {"none", "clear", "nothing", "ללא", "נקה", "אף אחד", "אף אחת"}


class Option(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str
    label_he: str
    label_en: str


class FieldDef(BaseModel):
    """One editable field as the CRM listed it."""

    model_config = ConfigDict(extra="ignore")

    field: str
    type: Literal["single", "multi"]
    label_he: str = ""
    label_en: str = ""
    options: list[Option] = Field(default_factory=list)


class Apartment(BaseModel):
    """A CRM apartment summary (the current version when it was read)."""

    model_config = ConfigDict(extra="ignore")

    apartment_id: int
    lineage_id: int
    building_address: Optional[str] = None
    apartment_name: Optional[str] = None
    floor: Optional[str] = None
    resident_name: Optional[str] = None
    jewish_status: Optional[str] = None
    language: list[str] = Field(default_factory=list)


class PendingEdit(BaseModel):
    """An update whose answer was lost: what was sent, to compare after a re-read."""

    model_config = ConfigDict(extra="forbid")

    field: str
    value: str | list[str]


class ApartmentEditState(BaseModel):
    """Held by the chat page between turns. Contains no token."""

    model_config = ConfigDict(extra="forbid")

    step: Step = "address"
    language: Language = "en"
    candidates: list[Apartment] = Field(default_factory=list)
    apartment: Optional[Apartment] = None
    fields: list[FieldDef] = Field(default_factory=list)
    field: Optional[str] = None
    blocked: Optional[PendingEdit] = None


MESSAGES = {
    "en": {
        "start": "Which apartment? Type its address (street and number). "
                 "You can also type the apartment number or the resident's name.",
        "no_match": "I found no apartment for \"{query}\" that you can edit. Try another address.",
        "choose_apartment": "I found several apartments. Which one?\n{list}",
        "truncated": "\n(Only the first {count} are shown - type a more exact address to narrow it down.)",
        "pick_number": "Please choose one of the numbers shown.",
        "apartment": "Apartment #{id}: {address}\n{values}",
        "choose_field": "Which field do you want to change?\n{list}",
        "no_fields": "Your organization doesn't let you edit any field of this apartment here. Nothing was changed.",
        "choose_single": "{label} - current value: {current}. Choose the new value:\n{list}",
        "choose_multi": "{label} - current: {current}. Choose one or more values, separated by commas "
                        "(e.g. 1,3), or \"none\" to clear:\n{list}",
        "saved": "Saved. The CRM now shows:\n{apartment}\n\nChoose another field to change, or pick something "
                 "else from the menu.",
        "unchanged": "Nothing to change - the CRM already has that value:\n{apartment}\n\nChoose another field, "
                     "or pick something else from the menu.",
        "stale": "This apartment was changed by someone else meanwhile, so nothing was saved. "
                 "Its current state:\n{apartment}\n\nChoose the value again.",
        "gone": "This apartment is no longer available to you, so nothing was saved.",
        "invalid": "The CRM did not accept that value for {label} ({code}). Nothing was saved. Choose again.",
        "forbidden": "The CRM did not allow this change ({message}). Nothing was saved.",
        "uncertain": "The CRM's answer was lost, so I don't know whether the change was saved. "
                     "I won't send it again. Reply \"yes\" to read the apartment's current state, "
                     "or \"no\" to stop.",
        "blocked_wait": "First I need to settle the earlier change, whose answer was lost. "
                        "Reply \"yes\" to check it, or \"no\" to stop.",
        "confirmed": "Checked: the CRM shows the change was saved:\n{apartment}\n\nChoose another field, or pick "
                     "something else from the menu.",
        "not_confirmed": "The CRM currently shows:\n{apartment}\n\nYour change ({label}: {wanted}) does not show. "
                         "It was probably not saved, but I can't be sure. Reply \"yes\" to check again, "
                         "or \"no\" to stop.",
        "stopped_blocked": "Stopped. Please check the apartment in the CRM before editing it again.",
        "not_sent": "I couldn't reach the CRM, so the change was not sent. Choose the value again to retry.",
        "cancelled": "Cancelled - nothing was changed.",
        "unauthorized": "Your CRM login is no longer valid. Please log in again. Nothing was changed.",
        "crm_not_configured": "The assistant is not connected to a CRM (CRM_API_URL is not set). Nothing was sent.",
        "crm_unreachable": "I couldn't reach the CRM. Nothing was changed; send your message again to retry.",
        "crm_timeout": "The CRM didn't answer in time. Nothing was changed; send your message again to retry.",
        "crm_failed": "The CRM gave an answer I can't use ({status}). Nothing was changed by this step.",
        "empty": "-",
        "address_parts": "{address}, apartment {number}, floor {floor}",
        "jewish_status": "Jewish status",
        "language": "Language",
        "resident": "Resident",
    },
    "he": {
        "start": "איזו דירה? כתבו את הכתובת (רחוב ומספר). אפשר גם לכתוב מספר דירה או שם דייר.",
        "no_match": "לא מצאתי דירה עבור \"{query}\" שאתם יכולים לערוך. נסו כתובת אחרת.",
        "choose_apartment": "מצאתי כמה דירות. איזו מהן?\n{list}",
        "truncated": "\n(מוצגות רק {count} הראשונות - כתבו כתובת מדויקת יותר כדי לצמצם.)",
        "pick_number": "אנא בחרו אחד מהמספרים שמוצגים.",
        "apartment": "דירה #{id}: {address}\n{values}",
        "choose_field": "איזה שדה תרצו לשנות?\n{list}",
        "no_fields": "הארגון שלכם אינו מאפשר לכם לערוך כאן אף שדה של הדירה הזו. לא שונה דבר.",
        "choose_single": "{label} - הערך הנוכחי: {current}. בחרו את הערך החדש:\n{list}",
        "choose_multi": "{label} - כרגע: {current}. בחרו ערך אחד או יותר, מופרדים בפסיקים (למשל 1,3), "
                        "או \"ללא\" כדי לנקות:\n{list}",
        "saved": "נשמר. ב-CRM מופיע עכשיו:\n{apartment}\n\nבחרו שדה נוסף לשינוי, או בחרו משהו אחר מהתפריט.",
        "unchanged": "אין מה לשנות - זה כבר הערך ב-CRM:\n{apartment}\n\nבחרו שדה אחר, או משהו אחר מהתפריט.",
        "stale": "מישהו אחר שינה את הדירה בינתיים, ולכן לא נשמר דבר. המצב הנוכחי:\n{apartment}\n\nבחרו את הערך שוב.",
        "gone": "הדירה הזו כבר אינה זמינה לכם, ולכן לא נשמר דבר.",
        "invalid": "ה-CRM לא קיבל את הערך עבור {label} ({code}). לא נשמר דבר. בחרו שוב.",
        "forbidden": "ה-CRM לא אישר את השינוי ({message}). לא נשמר דבר.",
        "uncertain": "התשובה מה-CRM אבדה, ולכן לא ידוע אם השינוי נשמר. לא אשלח אותו שוב. "
                     "השיבו \"כן\" כדי לקרוא את המצב הנוכחי של הדירה, או \"לא\" כדי להפסיק.",
        "blocked_wait": "קודם צריך לברר את השינוי הקודם, שהתשובה עליו אבדה. השיבו \"כן\" כדי לבדוק, או \"לא\" כדי להפסיק.",
        "confirmed": "נבדק: ב-CRM רואים שהשינוי נשמר:\n{apartment}\n\nבחרו שדה אחר, או משהו אחר מהתפריט.",
        "not_confirmed": "ב-CRM מופיע כרגע:\n{apartment}\n\nהשינוי שלכם ({label}: {wanted}) לא מופיע. "
                         "כנראה שלא נשמר, אבל אין לי ודאות. השיבו \"כן\" כדי לבדוק שוב, או \"לא\" כדי להפסיק.",
        "stopped_blocked": "הופסק. אנא בדקו את הדירה ב-CRM לפני שתערכו אותה שוב.",
        "not_sent": "לא הצלחתי להגיע ל-CRM, ולכן השינוי לא נשלח. בחרו את הערך שוב כדי לנסות שוב.",
        "cancelled": "בוטל - לא שונה דבר.",
        "unauthorized": "ההתחברות ל-CRM כבר אינה בתוקף. אנא התחברו שוב. לא שונה דבר.",
        "crm_not_configured": "העוזר אינו מחובר ל-CRM (CRM_API_URL לא הוגדר). לא נשלח דבר.",
        "crm_unreachable": "לא הצלחתי להגיע ל-CRM. לא שונה דבר; שלחו את ההודעה שוב כדי לנסות שוב.",
        "crm_timeout": "ה-CRM לא ענה בזמן. לא שונה דבר; שלחו את ההודעה שוב כדי לנסות שוב.",
        "crm_failed": "ה-CRM החזיר תשובה שאינני יכול להשתמש בה ({status}). שלב זה לא שינה דבר.",
        "empty": "—",
        "address_parts": "{address}, דירה {number}, קומה {floor}",
        "jewish_status": "סטטוס יהודי",
        "language": "שפה",
        "resident": "דייר",
    },
}


def say(language: str, key: str, **values) -> str:
    return MESSAGES[language][key].format(**values)


# --- display -----------------------------------------------------------------

def full_address(lang: str, apt: Apartment) -> str:
    empty = MESSAGES[lang]["empty"]
    return say(lang, "address_parts", address=apt.building_address or empty,
               number=apt.apartment_name or empty, floor=apt.floor or empty)


def _field_label(lang: str, field: FieldDef | None, name: str) -> str:
    if field is not None:
        label = field.label_he if lang == "he" else field.label_en
        if label and label != field.field:
            return label
    return MESSAGES[lang].get(name, name)


def _value_label(lang: str, field: FieldDef | None, value: str) -> str:
    for option in field.options if field else []:
        if option.value == value:
            return option.label_he if lang == "he" else option.label_en
    return value  # an option the admin has since turned off: shown as stored


def _values_text(lang: str, field: FieldDef | None, values) -> str:
    if isinstance(values, str):
        values = [values]
    shown = [_value_label(lang, field, v) for v in values or [] if v]
    return ", ".join(shown) if shown else MESSAGES[lang]["empty"]


def _current(apt: Apartment, name: str):
    return getattr(apt, name, None)


def apartment_text(lang: str, apt: Apartment, fields: list[FieldDef]) -> str:
    by_name = {f.field: f for f in fields}
    lines = []
    if apt.resident_name:
        lines.append(f"- {MESSAGES[lang]['resident']}: {apt.resident_name}")
    for name in ("jewish_status", "language"):
        field = by_name.get(name)
        lines.append(f"- {_field_label(lang, field, name)}: {_values_text(lang, field, _current(apt, name))}")
    return say(lang, "apartment", id=apt.apartment_id, address=full_address(lang, apt), values="\n".join(lines))


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {text}" for i, text in enumerate(items, 1))


# --- one turn ------------------------------------------------------------------

def _answer(state: ApartmentEditState, outcome: str, reply: str, *, choices=None, multi: bool = False,
            logout: bool = False, done: bool = False) -> dict:
    return {
        "intent": "edit_apartment",
        "outcome": outcome,
        "reply": reply,
        "state": state.model_dump(),
        # Buttons for the page: each sends its `message` as the next turn.
        "choices": choices or [],
        "multi": multi,
        "awaiting_check": state.step == "blocked",
        "logout": logout,
        "show_menu": done,
    }


def start(language: str) -> dict:
    """The menu choice "Edit an apartment": a fresh conversation, nothing sent anywhere."""
    state = ApartmentEditState(language=language)
    return _answer(state, "started", say(language, "start"))


def handle_turn(token: str, message: str, state: ApartmentEditState) -> dict:
    text = message.strip()
    state.language = detect_language(text, state.language, single_answer=state.step != "address")
    lang = state.language

    if state.step == "blocked":
        if is_explicit_yes(text):
            return _check_blocked(token, state)
        if is_explicit_no(text):
            return _answer(ApartmentEditState(language=lang), "stopped", say(lang, "stopped_blocked"), done=True)
        return _answer(state, "blocked", say(lang, "blocked_wait"))

    if is_explicit_no(text):
        return _answer(ApartmentEditState(language=lang), "cancelled", say(lang, "cancelled"), done=True)

    if state.step == "address":
        return _search(token, state, text)
    if state.step == "choose_apartment":
        index = _pick_index(text, len(state.candidates))
        if index is None:
            return _ask_apartment(state, prefix=say(lang, "pick_number") + "\n")
        return _choose_apartment(token, state, state.candidates[index])
    if state.step == "choose_field":
        field = _pick_field(text, state.fields)
        if field is None:
            return _ask_field(state, prefix=say(lang, "pick_number") + "\n")
        state.field = field.field
        state.step = "choose_value"
        return _ask_value(state)
    # choose_value
    field = _selected_field(state)
    if field is None:
        return _ask_field(state)
    value = _pick_value(text, field)
    if value is None:
        return _ask_value(state, prefix=say(lang, "pick_number") + "\n")
    return _update(token, state, field, value)


# --- picking -------------------------------------------------------------------

def _pick_index(text: str, count: int) -> Optional[int]:
    if text.isdigit() and 1 <= int(text) <= count:
        return int(text) - 1
    return None


def _matches(text: str, *labels: str) -> bool:
    wanted = text.casefold()
    return any(label and label.casefold() == wanted for label in labels)


def _pick_field(text: str, fields: list[FieldDef]) -> Optional[FieldDef]:
    index = _pick_index(text, len(fields))
    if index is not None:
        return fields[index]
    for field in fields:
        if _matches(text, field.field, field.label_he, field.label_en):
            return field
    return None


def _pick_option(token: str, options: list[Option]) -> Optional[str]:
    index = _pick_index(token, len(options))
    if index is not None:
        return options[index].value
    for option in options:
        if _matches(token, option.value, option.label_he, option.label_en):
            return option.value
    return None


def _pick_value(text: str, field: FieldDef):
    """A single value (str), a list for a multi field, or None if not understood."""
    if field.type == "single":
        return _pick_option(text, field.options)
    if " ".join(text.casefold().split()) in CLEAR_WORDS:
        return []
    parts = [p.strip() for p in re.split(r"[,،]", text) if p.strip()]
    tokens = []
    for part in parts:
        tokens.extend(part.split() if re.fullmatch(r"[\d\s]+", part) else [part])
    values = []
    for token in tokens:
        value = _pick_option(token, field.options)
        if value is None:
            return None
        if value not in values:
            values.append(value)
    return values or None


def _selected_field(state: ApartmentEditState) -> Optional[FieldDef]:
    return next((f for f in state.fields if f.field == state.field), None)


# --- asking --------------------------------------------------------------------

def _ask_apartment(state: ApartmentEditState, prefix: str = "", truncated: bool = False) -> dict:
    lang = state.language
    items = [full_address(lang, a) + (f" - {a.resident_name}" if a.resident_name else "") + f" (#{a.apartment_id})"
             for a in state.candidates]
    reply = prefix + say(lang, "choose_apartment", list=_numbered(items))
    if truncated:
        reply += say(lang, "truncated", count=len(items))
    choices = [{"label": f"{i}. {item}", "message": str(i)} for i, item in enumerate(items, 1)]
    return _answer(state, "choose_apartment", reply, choices=choices)


def _ask_field(state: ApartmentEditState, prefix: str = "", intro: str = "") -> dict:
    lang = state.language
    state.step = "choose_field"
    state.field = None
    labels = [_field_label(lang, f, f.field) for f in state.fields]
    reply = (intro + "\n\n" if intro else "") + prefix + say(lang, "choose_field", list=_numbered(labels))
    choices = [{"label": label, "message": str(i)} for i, label in enumerate(labels, 1)]
    return _answer(state, "choose_field", reply, choices=choices)


def _ask_value(state: ApartmentEditState, prefix: str = "", intro: str = "") -> dict:
    lang = state.language
    field = _selected_field(state)
    labels = [o.label_he if lang == "he" else o.label_en for o in field.options]
    current = _values_text(lang, field, _current(state.apartment, field.field))
    key = "choose_multi" if field.type == "multi" else "choose_single"
    reply = (intro + "\n\n" if intro else "") + prefix + say(
        lang, key, label=_field_label(lang, field, field.field), current=current, list=_numbered(labels))
    choices = [{"label": label, "message": str(i)} for i, label in enumerate(labels, 1)]
    return _answer(state, "choose_value", reply, choices=choices, multi=field.type == "multi")


# --- CRM steps -----------------------------------------------------------------

def _search(token: str, state: ApartmentEditState, query: str) -> dict:
    lang = state.language
    if not query:
        return _answer(state, "started", say(lang, "start"))
    result = crm_api.search_apartments(token, query)
    if result.body.get("status") != "ok":
        return _failure(state, result.body)
    results = [Apartment(**r) for r in result.body["results"]]
    if not results:
        return _answer(state, "no_match", say(lang, "no_match", query=query))
    if len(results) == 1:
        return _choose_apartment(token, state, results[0])
    state.step = "choose_apartment"
    state.candidates = results
    return _ask_apartment(state, truncated=result.body.get("truncated", False))


def _load_fields(token: str, state: ApartmentEditState) -> Optional[dict]:
    """Fresh from the CRM each time (the admin may change the locks or options). A failure answer, or None."""
    result = crm_api.apartment_fields(token)
    if result.body.get("status") != "ok":
        return _failure(state, result.body)
    state.fields = [FieldDef(**f) for f in result.body["fields"]]
    return None


def _choose_apartment(token: str, state: ApartmentEditState, apartment: Apartment) -> dict:
    lang = state.language
    state.apartment = apartment
    state.candidates = []
    failure = _load_fields(token, state)
    if failure:
        return failure
    shown = apartment_text(lang, apartment, state.fields)
    if not state.fields:
        return _answer(ApartmentEditState(language=lang), "no_fields", shown + "\n\n" + say(lang, "no_fields"),
                       done=True)
    return _ask_field(state, intro=shown)


def _reread(token: str, state: ApartmentEditState) -> tuple[Optional[Apartment], Optional[dict]]:
    """The apartment's current version by lineage. (apartment, None), (None, None) if gone, or (None, failure)."""
    result = crm_api.apartment_by_lineage(token, state.apartment.lineage_id)
    status = result.body.get("status")
    if status == "ok":
        return Apartment(**result.body["apartment"]), None
    if status == "not_found":
        return None, None
    return None, _failure(state, result.body)


def _update(token: str, state: ApartmentEditState, field: FieldDef, value) -> dict:
    lang = state.language
    result = crm_api.update_apartment_field(token, state.apartment.apartment_id, field.field, value)
    body = result.body
    status = body.get("status")

    if status in ("updated", "unchanged"):
        state.apartment = Apartment(**body["apartment"])  # what the CRM saved and read back
        key = "saved" if status == "updated" else "unchanged"
        return _ask_field(state, intro=say(lang, key, apartment=apartment_text(lang, state.apartment, state.fields)))
    if status in ("stale", "not_found"):
        apartment, failure = _reread(token, state)
        if failure:
            return failure
        if apartment is None:
            return _answer(ApartmentEditState(language=lang), "gone", say(lang, "gone"), done=True)
        state.apartment = apartment
        return _ask_value(state, intro=say(lang, "stale", apartment=apartment_text(lang, apartment, state.fields)))
    if status == "uncertain":
        state.step = "blocked"
        state.blocked = PendingEdit(field=field.field, value=value)
        return _answer(state, "uncertain", say(lang, "uncertain"))
    if status == "crm_unreachable":  # no connection: the update was never sent
        return _ask_value(state, intro=say(lang, "not_sent"))
    if status == "invalid":
        code = next((i.get("code") for i in body.get("invalid_fields") or [] if isinstance(i, dict)), "invalid")
        failure = _load_fields(token, state)  # the options may have changed
        if failure:
            return failure
        reply = say(lang, "invalid", label=_field_label(lang, field, field.field), code=code)
        if _selected_field(state) is None:
            return _ask_field(state, intro=reply)
        return _ask_value(state, intro=reply)
    if status == "forbidden":
        reply = say(lang, "forbidden", message=body.get("message") or status)
        failure = _load_fields(token, state)  # the admin may have locked the field
        if failure:
            return failure
        if not state.fields:
            return _answer(ApartmentEditState(language=lang), "forbidden", reply, done=True)
        return _ask_field(state, intro=reply)
    return _failure(state, body)


def _check_blocked(token: str, state: ApartmentEditState) -> dict:
    lang = state.language
    pending = state.blocked
    apartment, failure = _reread(token, state)
    if failure:
        return failure  # still blocked: the state keeps step "blocked"
    if apartment is None:
        return _answer(ApartmentEditState(language=lang), "gone", say(lang, "gone"), done=True)
    state.apartment = apartment
    field = next((f for f in state.fields if f.field == pending.field), None)
    current = _current(apartment, pending.field)
    saved = (sorted(current or []) == sorted(pending.value) if isinstance(pending.value, list)
             else current == pending.value)
    shown = apartment_text(lang, apartment, state.fields)
    if saved:
        state.blocked = None
        return _ask_field(state, intro=say(lang, "confirmed", apartment=shown))
    return _answer(state, "not_confirmed", say(
        lang, "not_confirmed", apartment=shown, label=_field_label(lang, field, pending.field),
        wanted=_values_text(lang, field, pending.value)))


def _failure(state: ApartmentEditState, body: dict) -> dict:
    lang = state.language
    status = body.get("status")
    if status == "unauthorized":
        return _answer(ApartmentEditState(language=lang), "unauthorized", say(lang, "unauthorized"), logout=True)
    if status == "forbidden":
        return _answer(ApartmentEditState(language=lang), "forbidden",
                       say(lang, "forbidden", message=body.get("message") or status), done=True)
    if status in ("crm_not_configured", "crm_unreachable", "crm_timeout"):
        return _answer(state, status, say(lang, status))
    return _answer(state, "crm_failed", say(lang, "crm_failed", status=status or "unknown"))
