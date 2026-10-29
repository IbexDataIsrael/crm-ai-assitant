"""The edit-an-apartment conversation (sheepsidebay_crm docs/fixes115.md;
docs/instructions18.md).

Stateless on the server, like the add-contact conversation
(app/contact_chat.py): the page holds the state and sends it with every turn;
the token comes separately and goes only to the bridge (app/crm_api.py).

The CRM decides which fields this user may edit on this apartment
(`editable_fields` in every apartment it returns), with their labels, kinds,
current values and options. Nothing here keeps a field name, label or option
list: the state holds only ids, the chosen field_key and the change waiting
for a yes. Every turn after the apartment is chosen re-reads it by lineage, so
the list is always the CRM's current one; if the list (or the apartment
version) differs from what the user was shown, the user is shown the new one
and asked again - a number typed for an old list is never applied.

Steps: address -> (choose_apartment) -> choose_field -> choose_value ->
confirm (old value -> new value, explicit yes) -> update -> the CRM's answer.

Safety rules:
- One update per explicit yes. The page disables input while a turn is in
  flight, and nothing here ever resends an update by itself.
- 409 (changed meanwhile) or 404: re-read by lineage (an apartment's id
  changes on every edit, its lineage id never does), show the current state,
  ask again. Never resend automatically.
- A lost answer (timeout, dropped connection, CRM 5xx, unreadable 2xx): the
  edit is BLOCKED. Nothing is resent; "yes" re-reads the lineage. Only if the
  CRM shows the value that was sent is it reported saved.

Wire values (sheepsidebay_crm backend/app/apartment_edit_rules.py parse_value):
yes_no is a JSON boolean; null clears text / number / date / yes_no; an empty
list clears multi_option; single_option can't be cleared. text and number are
sent as typed and checked by the CRM; a date typed day first (29/10/2026,
29.10.2026, 29-10-2026) is sent as 2026-10-29, anything else as typed.

Clearing is only through the page's "Clear the value" button (owner's choice,
29 Oct 2026): the button sends CLEAR_MESSAGE, so a typed "none" is an ordinary
value.
"""

import datetime
import hashlib
import json
import re
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from app import crm_api
from app.contact_chat import detect_language, is_explicit_no, is_explicit_yes

Language = Literal["en", "he"]
Step = Literal["address", "choose_apartment", "choose_field", "choose_value", "confirm", "blocked"]
KINDS = ("single_option", "multi_option", "text", "number", "date", "yes_no")
CLEARABLE = ("multi_option", "text", "number", "date", "yes_no")

# Sent only by the page's "Clear the value" button; empties a field (where the CRM allows it).
CLEAR_MESSAGE = "[clear]"
DAY_FIRST_DATE = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})")
# Answers to a yes/no FIELD (not the confirm step).
YES_VALUE_WORDS = {"yes", "y", "כן"}
NO_VALUE_WORDS = {"no", "n", "לא"}

# A value as sent to / read from the CRM.
Value = Union[StrictBool, StrictInt, StrictFloat, StrictStr, list[StrictStr], None]


class Option(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: Union[StrictInt, StrictStr]
    label: Optional[str] = None
    label_en: Optional[str] = None


class EditableField(BaseModel):
    """One entry of the CRM's editable_fields, as the CRM sent it."""

    model_config = ConfigDict(extra="ignore")

    field_key: str
    label: Optional[str] = None
    label_en: Optional[str] = None
    kind: str
    current_value: Any = None
    options: list[Option] = Field(default_factory=list)


class Apartment(BaseModel):
    """A CRM apartment result (search, by-lineage or the update answer)."""

    model_config = ConfigDict(extra="ignore")

    apartment_id: int
    lineage_id: int
    building_address: Optional[str] = None
    apartment_name: Optional[str] = None
    floor: Optional[str] = None
    resident_name: Optional[str] = None
    editable_fields: list[EditableField] = Field(default_factory=list)

    @property
    def fields(self) -> list[EditableField]:
        """The fields this chat can edit: every kind the CRM's contract names."""
        return [f for f in self.editable_fields if f.kind in KINDS]


class Candidate(BaseModel):
    """One search match while the user picks: ids and the line shown, nothing else."""

    model_config = ConfigDict(extra="forbid")

    apartment_id: int
    lineage_id: int
    text: str


class PendingChange(BaseModel):
    """The change on the confirm screen, or the one whose answer was lost."""

    model_config = ConfigDict(extra="forbid")

    field_key: str
    value: Value = None
    label: str = ""
    old_text: str = ""
    new_text: str = ""


class ApartmentEditState(BaseModel):
    """Held by the chat page between turns. No token, no field list."""

    model_config = ConfigDict(extra="forbid")

    step: Step = "address"
    language: Language = "en"
    candidates: list[Candidate] = Field(default_factory=list)
    truncated: bool = False
    apartment_id: Optional[int] = None
    lineage_id: Optional[int] = None
    field_key: Optional[str] = None
    # A fingerprint of the list the user was shown (fields, or the field's options).
    shown: Optional[str] = None
    pending: Optional[PendingChange] = None


MESSAGES = {
    "en": {
        "start": "Which apartment? Type its address (street and number). "
                 "You can also type the apartment number or the resident's name.",
        "no_match": "I found no apartment for \"{query}\" that you can see. Try another address.",
        "choose_apartment": "I found several apartments. Which one?",
        "truncated": "(Only the first {count} are shown - type a more exact address to narrow it down.)",
        "pick_number": "Please choose one of the numbers shown.",
        "apartment": "Apartment #{id}: {address}",
        "resident": " - {name}",
        "choose_field": "Which field do you want to change?",
        "no_fields": "Nothing in this apartment can be edited by you here. Nothing was changed.",
        "no_fields_left": "Nothing more in this apartment can be edited by you here.",
        "list_changed": "The apartment or the list of fields changed since it was shown, so I didn't use that "
                        "answer. Here is the current state:",
        "choose_single": "{label} - current value: {current}. Choose the new value:",
        "choose_multi": "{label} - current: {current}. Choose one or more values, separated by commas "
                        "(e.g. 1,3):",
        "choose_yes_no": "{label} - current value: {current}. Yes or no?",
        "type_text": "{label} - current value: {current}. Type the new value:",
        "type_number": "{label} - current value: {current}. Type the new number:",
        "type_date": "{label} - current value: {current}. Type the new date, day first "
                     "(for example 29/10/2026) or as 2026-10-29:",
        "not_understood_value": "I didn't understand that value.",
        "bad_date": "That is not a real date. Type it day first, for example 29/10/2026.",
        "cannot_clear": "This field can't be left empty - choose one of the values.",
        "confirm": "Change {label} of apartment #{id}:\nfrom: {old}\nto: {new}\n\n"
                   "Reply \"yes\" to save this change, or \"no\" to keep the current value.",
        "confirm_wait": "Reply \"yes\" to save exactly this change, or \"no\" to keep the current value.",
        "kept": "Not changed - {label} stays {old}.",
        "saved": "Saved. The CRM now shows {label}: {value}.",
        "saved_hidden": "Saved. After this change the CRM no longer lets you edit {label}, "
                        "so its value isn't shown here.",
        "unchanged": "Nothing changed - the CRM already had {label}: {value}.",
        "next": "Choose another field to change, or pick something else from the menu.",
        "stale": "This apartment was changed by someone else meanwhile, so nothing was saved. "
                 "Its current state:",
        "not_found_reread": "That version of the apartment is no longer current, so nothing was saved. "
                            "Its current state:",
        "gone": "This apartment is no longer available to you, so nothing was saved.",
        "read_forbidden": "The CRM did not allow this request. Nothing was changed.",
        "invalid": "The CRM did not accept the value. Nothing was saved.\n{reason}",
        "forbidden": "The CRM says {label} can't be edited here. Nothing was saved. I reloaded the apartment:",
        "uncertain": "The CRM's answer was lost, so I don't know whether the change was saved. "
                     "I won't send it again. Reply \"yes\" to read the apartment's current state, "
                     "or \"no\" to stop.",
        "blocked_wait": "First I need to settle the earlier change, whose answer was lost. "
                        "Reply \"yes\" to check it, or \"no\" to stop.",
        "confirmed": "Checked: the CRM shows the change was saved ({label}: {value}).",
        "not_confirmed": "The CRM currently shows {label}: {current}. Your change ({wanted}) does not show. "
                         "It was probably not saved, but I can't be sure. Reply \"yes\" to check again, "
                         "or \"no\" to stop.",
        "not_visible": "The CRM no longer lists {label} as editable for you, so I can't see whether your "
                       "change ({wanted}) was saved. Reply \"yes\" to check again, or \"no\" to stop.",
        "stopped_blocked": "Stopped. Please check the apartment in the CRM before editing it again.",
        "not_sent": "I couldn't reach the CRM, so the change was not sent. Reply \"yes\" to try again, "
                    "or \"no\" to keep the current value.",
        "cancelled": "Cancelled - nothing was changed.",
        "unauthorized": "Your CRM login is no longer valid. Please log in again. Nothing was changed.",
        "crm_not_configured": "The assistant is not connected to a CRM (CRM_API_URL is not set). Nothing was sent.",
        "crm_unreachable": "I couldn't reach the CRM. Nothing was changed; send your message again to retry.",
        "crm_timeout": "The CRM didn't answer in time. Nothing was changed; send your message again to retry.",
        "crm_failed": "The CRM gave an answer I can't use ({status}). Nothing was changed by this step.",
        "empty": "-",
        "yes": "Yes",
        "no": "No",
        "clear": "Clear the value",
        "address_parts": "{address}, apartment {number}, floor {floor}",
        "code": {
            "expected_option": "{label}: choose one of the listed values.",
            "expected_list": "{label}: choose one or more of the listed values.",
            "value_not_allowed": "{label}: this value is not one of the allowed values.",
            "expected_text": "{label}: this field takes text.",
            "expected_number": "{label}: this field takes a number (for example 12 or 3.5).",
            "expected_date": "{label}: this field takes a date like 2026-10-29.",
            "expected_yes_no": "{label}: this field takes yes or no.",
            "invalid_format": "{label}: the format is not valid.",
            "other": "{label}: the value was refused ({code}).",
        },
    },
    "he": {
        "start": "איזו דירה? כתבו את הכתובת (רחוב ומספר). אפשר גם לכתוב מספר דירה או שם דייר.",
        "no_match": "לא מצאתי דירה עבור \"{query}\" שאתם יכולים לראות. נסו כתובת אחרת.",
        "choose_apartment": "מצאתי כמה דירות. איזו מהן?",
        "truncated": "(מוצגות רק {count} הראשונות - כתבו כתובת מדויקת יותר כדי לצמצם.)",
        "pick_number": "אנא בחרו אחד מהמספרים שמוצגים.",
        "apartment": "דירה #{id}: {address}",
        "resident": " - {name}",
        "choose_field": "איזה שדה תרצו לשנות?",
        "no_fields": "אין בדירה הזו שום שדה שאתם יכולים לערוך כאן. לא שונה דבר.",
        "no_fields_left": "אין בדירה הזו עוד שדה שאתם יכולים לערוך כאן.",
        "list_changed": "הדירה או רשימת השדות השתנו מאז שהוצגו, ולכן לא השתמשתי בתשובה. זה המצב הנוכחי:",
        "choose_single": "{label} - הערך הנוכחי: {current}. בחרו את הערך החדש:",
        "choose_multi": "{label} - כרגע: {current}. בחרו ערך אחד או יותר, מופרדים בפסיקים (למשל 1,3):",
        "choose_yes_no": "{label} - הערך הנוכחי: {current}. כן או לא?",
        "type_text": "{label} - הערך הנוכחי: {current}. כתבו את הערך החדש:",
        "type_number": "{label} - הערך הנוכחי: {current}. כתבו את המספר החדש:",
        "type_date": "{label} - הערך הנוכחי: {current}. כתבו את התאריך החדש, יום קודם "
                     "(למשל 29/10/2026) או בצורה 2026-10-29:",
        "not_understood_value": "לא הבנתי את הערך.",
        "bad_date": "זה אינו תאריך קיים. כתבו אותו יום קודם, למשל 29/10/2026.",
        "cannot_clear": "אי אפשר להשאיר את השדה הזה ריק - בחרו אחד מהערכים.",
        "confirm": "שינוי {label} בדירה #{id}:\nמ: {old}\nל: {new}\n\n"
                   "השיבו \"כן\" כדי לשמור את השינוי, או \"לא\" כדי להשאיר את הערך הנוכחי.",
        "confirm_wait": "השיבו \"כן\" כדי לשמור בדיוק את השינוי הזה, או \"לא\" כדי להשאיר את הערך הנוכחי.",
        "kept": "לא שונה - {label} נשאר {old}.",
        "saved": "נשמר. ב-CRM מופיע עכשיו {label}: {value}.",
        "saved_hidden": "נשמר. אחרי השינוי ה-CRM כבר לא מאפשר לכם לערוך את {label}, ולכן הערך לא מוצג כאן.",
        "unchanged": "לא שונה דבר - ב-CRM כבר היה {label}: {value}.",
        "next": "בחרו שדה נוסף לשינוי, או בחרו משהו אחר מהתפריט.",
        "stale": "מישהו אחר שינה את הדירה בינתיים, ולכן לא נשמר דבר. המצב הנוכחי:",
        "not_found_reread": "הגרסה הזו של הדירה כבר אינה עדכנית, ולכן לא נשמר דבר. המצב הנוכחי:",
        "gone": "הדירה הזו כבר אינה זמינה לכם, ולכן לא נשמר דבר.",
        "read_forbidden": "ה-CRM לא אישר את הבקשה. לא שונה דבר.",
        "invalid": "ה-CRM לא קיבל את הערך. לא נשמר דבר.\n{reason}",
        "forbidden": "ה-CRM מדווח שאי אפשר לערוך כאן את {label}. לא נשמר דבר. טענתי את הדירה מחדש:",
        "uncertain": "התשובה מה-CRM אבדה, ולכן לא ידוע אם השינוי נשמר. לא אשלח אותו שוב. "
                     "השיבו \"כן\" כדי לקרוא את המצב הנוכחי של הדירה, או \"לא\" כדי להפסיק.",
        "blocked_wait": "קודם צריך לברר את השינוי הקודם, שהתשובה עליו אבדה. השיבו \"כן\" כדי לבדוק, או \"לא\" כדי להפסיק.",
        "confirmed": "נבדק: ב-CRM רואים שהשינוי נשמר ({label}: {value}).",
        "not_confirmed": "ב-CRM מופיע כרגע {label}: {current}. השינוי שלכם ({wanted}) לא מופיע. "
                         "כנראה שלא נשמר, אבל אין לי ודאות. השיבו \"כן\" כדי לבדוק שוב, או \"לא\" כדי להפסיק.",
        "not_visible": "ה-CRM כבר לא מציג לכם את {label} כשדה לעריכה, ולכן אינני רואה אם השינוי שלכם ({wanted}) "
                       "נשמר. השיבו \"כן\" כדי לבדוק שוב, או \"לא\" כדי להפסיק.",
        "stopped_blocked": "הופסק. אנא בדקו את הדירה ב-CRM לפני שתערכו אותה שוב.",
        "not_sent": "לא הצלחתי להגיע ל-CRM, ולכן השינוי לא נשלח. השיבו \"כן\" כדי לנסות שוב, "
                    "או \"לא\" כדי להשאיר את הערך הנוכחי.",
        "cancelled": "בוטל - לא שונה דבר.",
        "unauthorized": "ההתחברות ל-CRM כבר אינה בתוקף. אנא התחברו שוב. לא שונה דבר.",
        "crm_not_configured": "העוזר אינו מחובר ל-CRM (CRM_API_URL לא הוגדר). לא נשלח דבר.",
        "crm_unreachable": "לא הצלחתי להגיע ל-CRM. לא שונה דבר; שלחו את ההודעה שוב כדי לנסות שוב.",
        "crm_timeout": "ה-CRM לא ענה בזמן. לא שונה דבר; שלחו את ההודעה שוב כדי לנסות שוב.",
        "crm_failed": "ה-CRM החזיר תשובה שאינני יכול להשתמש בה ({status}). שלב זה לא שינה דבר.",
        "empty": "—",
        "yes": "כן",
        "no": "לא",
        "clear": "ניקוי הערך",
        "address_parts": "{address}, דירה {number}, קומה {floor}",
        "code": {
            "expected_option": "{label}: בחרו אחד מהערכים ברשימה.",
            "expected_list": "{label}: בחרו ערך אחד או יותר מהרשימה.",
            "value_not_allowed": "{label}: הערך הזה אינו אחד מהערכים המותרים.",
            "expected_text": "{label}: השדה מקבל טקסט.",
            "expected_number": "{label}: השדה מקבל מספר (למשל 12 או 3.5).",
            "expected_date": "{label}: השדה מקבל תאריך כמו 2026-10-29.",
            "expected_yes_no": "{label}: השדה מקבל כן או לא.",
            "invalid_format": "{label}: הפורמט אינו תקין.",
            "other": "{label}: הערך נדחה ({code}).",
        },
    },
}


def say(language: str, key: str, **values) -> str:
    return MESSAGES[language][key].format(**values)


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


# --- display -----------------------------------------------------------------

def full_address(lang: str, apt: Apartment) -> str:
    empty = MESSAGES[lang]["empty"]
    return say(lang, "address_parts", address=apt.building_address or empty,
               number=apt.apartment_name or empty, floor=apt.floor or empty)


def field_label(lang: str, field: EditableField) -> str:
    first, second = (field.label, field.label_en) if lang == "he" else (field.label_en, field.label)
    return first or second or field.field_key


def option_label(lang: str, option: Option) -> str:
    first, second = (option.label, option.label_en) if lang == "he" else (option.label_en, option.label)
    return first or second or str(option.value)


def _is_empty(value) -> bool:
    return value is None or value == "" or value == []


def value_text(lang: str, field: Optional[EditableField], value) -> str:
    """A value as the user reads it: option labels, yes/no words, else the value as the CRM gave it."""
    if _is_empty(value):
        return MESSAGES[lang]["empty"]
    if isinstance(value, list):
        return ", ".join(value_text(lang, field, v) for v in value)
    if isinstance(value, bool):
        return MESSAGES[lang]["yes" if value else "no"]
    for option in field.options if field else []:
        if str(option.value) == str(value):
            return option_label(lang, option)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)  # also an option the admin has since turned off: shown as stored


def apartment_text(lang: str, apt: Apartment) -> str:
    head = say(lang, "apartment", id=apt.apartment_id, address=full_address(lang, apt))
    if apt.resident_name:
        head += say(lang, "resident", name=apt.resident_name)
    lines = [f"- {field_label(lang, f)}: {value_text(lang, f, f.current_value)}" for f in apt.fields]
    return "\n".join([head, *lines])


def _numbered(items: list[str]) -> list[str]:
    return [f"{i}. {text}" for i, text in enumerate(items, 1)]


def _fingerprint(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _fields_fingerprint(apt: Apartment) -> str:
    return _fingerprint([apt.apartment_id, [[f.field_key, f.kind] for f in apt.fields]])


def _field_fingerprint(apt: Apartment, field: EditableField) -> str:
    return _fingerprint([apt.apartment_id, field.field_key, field.kind, [o.value for o in field.options]])


# --- one turn ------------------------------------------------------------------

def _answer(state: ApartmentEditState, outcome: str, prompt: str, *, intro: str = "", items=None,
            choices=None, multi: bool = False, clear=None, logout: bool = False,
            done: bool = False) -> dict:
    """`reply` is the whole text (a numbered list included, for any text channel); `prompt` is the
    same without the list, for the page, which shows `choices` as buttons instead."""
    lines = items or []
    head = (intro + "\n\n" if intro else "") + prompt
    return {
        "intent": "edit_apartment",
        "outcome": outcome,
        "reply": "\n".join([head, *lines]),
        "prompt": head,
        "state": state.model_dump(),
        # Buttons for the page: each sends its `message` as the next turn.
        "choices": choices if choices is not None else [
            {"label": line, "message": str(i)} for i, line in enumerate(lines, 1)],
        "multi": multi,
        # The "Clear the value" button (also in `choices`, except for a multi list's Save with none ticked).
        "clear": clear,
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

    if state.step == "confirm":
        if is_explicit_yes(text):
            return _update(token, state)
        if is_explicit_no(text):
            pending = state.pending
            return _reload(token, state, say(lang, "kept", label=pending.label, old=pending.old_text))
        return _answer(state, "confirm", say(lang, "confirm_wait"), choices=_yes_no_buttons(lang))

    if state.step == "address":
        if is_explicit_no(text):
            return _cancel(state)
        return _search(token, state, text)

    if state.step == "choose_apartment":
        index = _pick_index(text, len(state.candidates))
        if index is None:
            if is_explicit_no(text):
                return _cancel(state)
            return _ask_apartment(state, prefix=say(lang, "pick_number"))
        return _open_lineage(token, state, state.candidates[index].lineage_id)

    # choose_field / choose_value: the CRM's current list, read fresh.
    apt, failure = _reread(token, state)
    if failure:
        return failure
    if state.step == "choose_field":
        if state.shown != _fields_fingerprint(apt):
            return _ask_field(state, apt, intro=say(lang, "list_changed") + "\n" + apartment_text(lang, apt))
        field = _pick_field(lang, text, apt.fields)
        if field is None:
            if is_explicit_no(text):
                return _cancel(state)
            return _ask_field(state, apt, prefix=say(lang, "pick_number"))
        return _ask_value(state, apt, field)

    # choose_value
    field = next((f for f in apt.fields if f.field_key == state.field_key), None)
    if field is None or state.shown != _field_fingerprint(apt, field):
        return _ask_field(state, apt, intro=say(lang, "list_changed") + "\n" + apartment_text(lang, apt))
    understood, value, problem = _pick_value(lang, text, field)
    if not understood:
        if is_explicit_no(text):
            return _cancel(state)
        return _ask_value(state, apt, field, prefix=say(lang, problem))
    return _confirm_screen(state, apt, field, value)


def _cancel(state: ApartmentEditState) -> dict:
    lang = state.language
    return _answer(ApartmentEditState(language=lang), "cancelled", say(lang, "cancelled"), done=True)


def _yes_no_buttons(lang: str) -> list[dict]:
    return [{"label": MESSAGES[lang]["yes"], "message": "yes" if lang == "en" else "כן"},
            {"label": MESSAGES[lang]["no"], "message": "no" if lang == "en" else "לא"}]


# --- picking -------------------------------------------------------------------

def _pick_index(text: str, count: int) -> Optional[int]:
    if text.isdigit() and 1 <= int(text) <= count:
        return int(text) - 1
    return None


def _matches(text: str, *labels) -> bool:
    wanted = _normalized(text)
    return any(label is not None and _normalized(str(label)) == wanted for label in labels)


def _pick_field(lang: str, text: str, fields: list[EditableField]) -> Optional[EditableField]:
    index = _pick_index(text, len(fields))
    if index is not None:
        return fields[index]
    return next((f for f in fields if _matches(text, f.label, f.label_en)), None)


def _pick_option(token: str, options: list[Option]):
    """An option's value (as the CRM gave it, str or int), or None."""
    index = _pick_index(token, len(options))
    if index is not None:
        return options[index].value
    for option in options:
        if _matches(token, option.value, option.label, option.label_en):
            return option.value
    return None


def _pick_value(lang: str, text: str, field: EditableField) -> tuple[bool, Value, str]:
    """(understood, value to send, message key if not understood). None is a real value: it clears."""
    kind = field.kind
    clear = text == CLEAR_MESSAGE
    if clear and kind in CLEARABLE:
        return True, ([] if kind == "multi_option" else None), ""
    if kind == "single_option":
        value = _pick_option(text, field.options)
        if value is None:
            return False, None, "cannot_clear" if clear else "pick_number"
        return True, value, ""
    if kind == "multi_option":
        parts = [p.strip() for p in re.split(r"[,،]", text) if p.strip()]
        tokens = []
        for part in parts:
            tokens.extend(part.split() if re.fullmatch(r"[\d\s]+", part) else [part])
        values = []
        for token in tokens:
            value = _pick_option(token, field.options)
            if value is None:
                return False, None, "pick_number"
            if str(value) not in values:
                values.append(str(value))
        return (True, values, "") if values else (False, None, "pick_number")
    if kind == "yes_no":
        word = _normalized(text)
        if word == "1" or word in YES_VALUE_WORDS:
            return True, True, ""
        if word == "2" or word in NO_VALUE_WORDS:
            return True, False, ""
        return False, None, "not_understood_value"
    # text / number / date: as typed (a day-first date as YYYY-MM-DD); the CRM checks it.
    if not text:
        return False, None, "not_understood_value"
    if kind == "date":
        day_first = DAY_FIRST_DATE.fullmatch(text)
        if day_first:
            day, month, year = map(int, day_first.groups())
            try:
                return True, datetime.date(year, month, day).isoformat(), ""
            except ValueError:
                return False, None, "bad_date"
    return True, text, ""


# --- asking --------------------------------------------------------------------

def _ask_apartment(state: ApartmentEditState, prefix: str = "") -> dict:
    lang = state.language
    prompt = (prefix + "\n" if prefix else "") + say(lang, "choose_apartment")
    items = _numbered([c.text for c in state.candidates])
    if state.truncated:
        items.append(say(lang, "truncated", count=len(state.candidates)))
    choices = [{"label": line, "message": str(i)} for i, line in enumerate(items[:len(state.candidates)], 1)]
    return _answer(state, "choose_apartment", prompt, items=items, choices=choices)


def _ask_field(state: ApartmentEditState, apt: Apartment, prefix: str = "", intro: str = "",
               after_change: bool = False) -> dict:
    lang = state.language
    state.step = "choose_field"
    state.apartment_id, state.lineage_id = apt.apartment_id, apt.lineage_id
    state.field_key = None
    state.pending = None
    state.candidates = []
    if not apt.fields:
        key = "no_fields_left" if after_change else "no_fields"
        shown = intro or apartment_text(lang, apt)
        return _answer(ApartmentEditState(language=lang), "no_fields", say(lang, key), intro=shown, done=True)
    state.shown = _fields_fingerprint(apt)
    prompt = (prefix + "\n" if prefix else "") + say(lang, "choose_field")
    return _answer(state, "choose_field", prompt, intro=intro,
                   items=_numbered([field_label(lang, f) for f in apt.fields]))


def _ask_value(state: ApartmentEditState, apt: Apartment, field: EditableField, prefix: str = "",
               intro: str = "") -> dict:
    lang = state.language
    state.step = "choose_value"
    state.apartment_id, state.lineage_id = apt.apartment_id, apt.lineage_id
    state.field_key = field.field_key
    state.pending = None
    state.shown = _field_fingerprint(apt, field)
    kind = field.kind
    key = {"single_option": "choose_single", "multi_option": "choose_multi", "yes_no": "choose_yes_no",
           "text": "type_text", "number": "type_number", "date": "type_date"}[kind]
    prompt = (prefix + "\n" if prefix else "") + say(
        lang, key, label=field_label(lang, field), current=value_text(lang, field, field.current_value))
    items, choices = [], None
    if kind in ("single_option", "multi_option"):
        items = _numbered([option_label(lang, o) for o in field.options])
    elif kind == "yes_no":
        items = _numbered([MESSAGES[lang]["yes"], MESSAGES[lang]["no"]])
    clear = {"label": MESSAGES[lang]["clear"], "message": CLEAR_MESSAGE} if kind in CLEARABLE else None
    if clear and kind != "multi_option":
        choices = [{"label": line, "message": str(i)} for i, line in enumerate(items, 1)]
        choices.append(clear)
    return _answer(state, "choose_value", prompt, intro=intro, items=items, choices=choices,
                   multi=kind == "multi_option", clear=clear)


def _confirm_screen(state: ApartmentEditState, apt: Apartment, field: EditableField, value) -> dict:
    lang = state.language
    label = field_label(lang, field)
    state.step = "confirm"
    state.pending = PendingChange(field_key=field.field_key, value=value, label=label,
                                  old_text=value_text(lang, field, field.current_value),
                                  new_text=value_text(lang, field, value))
    reply = say(lang, "confirm", label=label, id=apt.apartment_id, old=state.pending.old_text,
                new=state.pending.new_text)
    return _answer(state, "confirm", reply, choices=_yes_no_buttons(lang))


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
        return _ask_field(state, results[0], intro=apartment_text(lang, results[0]))
    state.step = "choose_apartment"
    state.truncated = result.body.get("truncated", False)
    state.candidates = [
        Candidate(apartment_id=a.apartment_id, lineage_id=a.lineage_id,
                  text=full_address(lang, a) + (say(lang, "resident", name=a.resident_name) if a.resident_name else "")
                  + f" (#{a.apartment_id})")
        for a in results
    ]
    return _ask_apartment(state)


def _read_lineage(token: str, state: ApartmentEditState, lineage_id: int):
    """(apartment, None), (None, None) if gone, or (None, failure answer)."""
    result = crm_api.apartment_by_lineage(token, lineage_id)
    status = result.body.get("status")
    if status == "ok":
        return Apartment(**result.body["apartment"]), None
    if status == "not_found":
        return None, None
    return None, _failure(state, result.body)


def _gone(state: ApartmentEditState) -> dict:
    lang = state.language
    return _answer(ApartmentEditState(language=lang), "gone", say(lang, "gone"), done=True)


def _reread(token: str, state: ApartmentEditState):
    """The chosen apartment, fresh. (apartment, None) or (None, answer to return)."""
    if state.lineage_id is None:
        return None, _answer(ApartmentEditState(language=state.language), "started", say(state.language, "start"))
    apt, failure = _read_lineage(token, state, state.lineage_id)
    if failure:
        return None, failure
    if apt is None:
        return None, _gone(state)
    return apt, None


def _open_lineage(token: str, state: ApartmentEditState, lineage_id: int) -> dict:
    apt, failure = _read_lineage(token, state, lineage_id)
    if failure:
        return failure
    if apt is None:
        return _gone(state)
    return _ask_field(state, apt, intro=apartment_text(state.language, apt))


def _reload(token: str, state: ApartmentEditState, message: str, ask_value_for: Optional[str] = None) -> dict:
    """Re-read the apartment, show it after `message`, and ask for the same field's value
    again (if it is still editable and `ask_value_for` is set) or for a field."""
    lang = state.language
    apt, failure = _reread(token, state)
    if failure:
        return failure
    intro = message + "\n" + apartment_text(lang, apt)
    field = next((f for f in apt.fields if f.field_key == ask_value_for), None)
    if field is not None:
        return _ask_value(state, apt, field, intro=intro)
    return _ask_field(state, apt, intro=intro, after_change=True)


def _update(token: str, state: ApartmentEditState) -> dict:
    lang = state.language
    pending = state.pending
    result = crm_api.update_apartment_field(token, state.apartment_id, pending.field_key, pending.value)
    body = result.body
    status = body.get("status")

    if status in ("updated", "unchanged"):
        apt = Apartment(**body["apartment"])  # what the CRM saved and read back
        field = next((f for f in apt.fields if f.field_key == pending.field_key), None)
        if status == "updated" and (body.get("value") is None and field is None):
            line = say(lang, "saved_hidden", label=pending.label)
        else:
            shown = value_text(lang, field, body.get("value"))
            line = say(lang, "saved" if status == "updated" else "unchanged",
                       label=field_label(lang, field) if field else pending.label, value=shown)
        answer = _ask_field(state, apt, prefix=say(lang, "next"), intro=line + "\n" + apartment_text(lang, apt),
                            after_change=True)
        if answer["outcome"] == "choose_field":
            answer["outcome"] = status
        return answer
    if status == "stale":
        return _reload(token, state, say(lang, "stale"), ask_value_for=pending.field_key)
    if status == "not_found":
        return _reload(token, state, say(lang, "not_found_reread"), ask_value_for=pending.field_key)
    if status == "uncertain":
        state.step = "blocked"
        return _answer(state, "uncertain", say(lang, "uncertain"))
    if status == "crm_unreachable":  # no connection: the update was never sent
        return _answer(state, "not_sent", say(lang, "not_sent"), choices=_yes_no_buttons(lang))
    if status == "invalid":
        codes = MESSAGES[lang]["code"]
        reasons = []
        for item in body.get("invalid_fields") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "invalid")
            reasons.append(codes.get(code, codes["other"]).format(label=pending.label, code=code))
        reply = say(lang, "invalid", reason="\n".join(reasons) or codes["other"].format(
            label=pending.label, code="invalid"))
        return _reload(token, state, reply, ask_value_for=pending.field_key)
    if status == "forbidden":
        # The CRM gives one refusal for every reason (hidden, locked, unknown, not assigned): never explained here.
        return _reload(token, state, say(lang, "forbidden", label=pending.label))
    return _failure(state, body)


def same_value(kind: str, current, sent) -> bool:
    """Whether the CRM's current value is the value that was sent (after a lost answer)."""
    if _is_empty(current) and _is_empty(sent):
        return True
    if kind == "multi_option":
        return sorted(map(str, current or [])) == sorted(map(str, sent or []))
    if kind == "yes_no":
        return current is sent
    if kind == "number":
        try:
            return float(current) == float(sent)
        except (TypeError, ValueError):
            return False
    if kind == "date":
        return str(current)[:10] == str(sent).strip()[:10]
    return str(current).strip() == str(sent).strip()


def _check_blocked(token: str, state: ApartmentEditState) -> dict:
    lang = state.language
    pending = state.pending
    apt, failure = _reread(token, state)
    if failure:
        return failure  # a gone apartment ends; any other failure keeps step "blocked"
    field = next((f for f in apt.fields if f.field_key == pending.field_key), None)
    if field is None:
        return _answer(state, "not_confirmed", say(lang, "not_visible", label=pending.label,
                                                    wanted=pending.new_text))
    if same_value(field.kind, field.current_value, pending.value):
        line = say(lang, "confirmed", label=field_label(lang, field),
                   value=value_text(lang, field, field.current_value))
        return _ask_field(state, apt, intro=line + "\n" + apartment_text(lang, apt), after_change=True)
    return _answer(state, "not_confirmed", say(
        lang, "not_confirmed", label=field_label(lang, field), current=value_text(lang, field, field.current_value),
        wanted=pending.new_text))


def _failure(state: ApartmentEditState, body: dict) -> dict:
    lang = state.language
    status = body.get("status")
    if status == "unauthorized":
        return _answer(ApartmentEditState(language=lang), "unauthorized", say(lang, "unauthorized"), logout=True)
    if status == "forbidden":
        return _answer(ApartmentEditState(language=lang), "forbidden", say(lang, "read_forbidden"), done=True)
    if status in ("crm_not_configured", "crm_unreachable", "crm_timeout"):
        return _answer(state, status, say(lang, status))
    return _answer(state, "crm_failed", say(lang, "crm_failed", status=status or "unknown"))
