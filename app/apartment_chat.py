"""The edit-an-apartment conversation (sheepsidebay_crm docs/fixes115.md and
fixes118.md; docs/instructions18.md).

Stateless on the server, like the add-contact conversation
(app/contact_chat.py): the page holds the state and sends it with every turn;
the token comes separately and goes only to the bridge (app/crm_api.py).

The CRM decides which fields this user may edit on this apartment
(`editable_fields` in every apartment it returns), with their labels (one
`label`, in the user's CRM language), kinds, current values and options.
Nothing here keeps a field name, label or option list: the state holds only
ids, the chosen field_key and the change waiting for a yes. Every turn after
the apartment is chosen re-reads it by lineage, so the list is always the
CRM's current one; if the list (or the apartment version) differs from what
the user was shown, the user is shown the new one and asked again - a number
typed for an old list is never applied.

Steps: address -> (choose_apartment) -> choose_field -> choose_value ->
confirm (old value -> new value, explicit yes) -> update -> the CRM's answer.

Safety rules:
- One update per explicit yes. The page disables input while a turn is in
  flight, and nothing here ever sends a new update by itself.
- Every confirmed change carries a request_id (fixes118 decision 6), made at
  the first yes and kept in the state until the CRM's answer settles it.
- 409 (changed meanwhile) or 404: re-read by lineage (an apartment's id
  changes on every edit, its lineage id never does), show the current state,
  ask again.
- A lost answer (timeout, dropped connection, CRM 5xx, unreadable 2xx): the
  edit is BLOCKED. The next "yes" sends the SAME request_id with the SAME
  body: the CRM answers from its record (`replayed`) and can never save it
  twice, so its answer is the proof. A matching value alone never is.

Wire values (sheepsidebay_crm backend/app/apartment_edit_rules.py parse_value):
yes_no is a JSON boolean and single_option one option value, neither ever
cleared; an empty list clears multi_option; null clears text / number / date.
text and number are sent as typed and checked by the CRM; a date typed day
first (29/10/2026, 29.10.2026, 29-10-2026) is sent as 2026-10-29, anything
else as typed.

Clearing is only through the page's "Clear the value" button (owner's choice,
29 Oct 2026): the button sends CLEAR_MESSAGE, so a typed "none" is an ordinary
value.
"""

import datetime
import hashlib
import json
import re
import uuid
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from app import crm_api
from app.contact_chat import is_explicit_no, is_explicit_yes

Language = Literal["en", "he"]
Step = Literal["address", "choose_apartment", "choose_field", "choose_value", "confirm", "blocked"]
KINDS = ("single_option", "multi_option", "text", "number", "date", "yes_no")
CLEARABLE = ("multi_option", "text", "number", "date")

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


class EditableField(BaseModel):
    """One entry of the CRM's editable_fields, as the CRM sent it."""

    model_config = ConfigDict(extra="ignore")

    field_key: str
    label: Optional[str] = None
    kind: str
    current_value: Any = None
    options: list[Option] = Field(default_factory=list)


class Apartment(BaseModel):
    """A CRM apartment result (search, getapartment or the update answer)."""

    model_config = ConfigDict(extra="ignore")

    apartment_id: int
    lineage_id: int
    building_address: Optional[str] = None
    apartment_name: Optional[str] = None
    floor: Optional[str] = None
    resident_name: Optional[str] = None
    # The CRM web page that always opens the current version (fixes118 decision 8); checked
    # only when a link is built (crm_api.web_link), so an odd value just means no link.
    web_path: Any = None
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
    # Made at the first yes; the same id goes with the same body until the CRM settles it.
    request_id: Optional[str] = None
    # The version the update was sent for (part of what the CRM matches a request_id with).
    apartment_id: Optional[int] = None


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
        "field_unclear": "I couldn't tell which field \"{words}\" is - please choose it from the list.",
        "value_unclear": "I couldn't use \"{value}\" as the {label} - please choose or type the value.",
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
        "confirm": "Change {label} of {place}:\nfrom: {old}\nto: {new}\n\n"
                   "Reply \"yes\" to save this change, or \"no\" to keep the current value.",
        "confirm_wait": "Reply \"yes\" to save exactly this change, or \"no\" to keep the current value.",
        "kept": "Not changed - {label} stays {old}.",
        "saved": "Saved. The CRM now shows {label}: {value}.",
        "saved_hidden": "Saved. After this change the CRM no longer lets you edit {label}, "
                        "so its value isn't shown here.",
        "unchanged": "Nothing changed - the CRM already had {label}: {value}.",
        "next": "Choose another field to change, or press Cancel to search for something else.",
        "stale": "This apartment was changed by someone else meanwhile, so nothing was saved. "
                 "Its current state:",
        "not_found_reread": "That version of the apartment is no longer current, so nothing was saved. "
                            "Its current state:",
        "gone": "This apartment is no longer available to you, so nothing was saved.",
        "read_forbidden": "The CRM did not allow this request. Nothing was changed.",
        "invalid": "The CRM did not accept the value. Nothing was saved.\n{reason}",
        "forbidden": "The CRM says {label} can't be edited here. Nothing was saved. I reloaded the apartment:",
        "reused": "The CRM refused the request because its request id was already used for a different "
                  "change. Nothing was saved. This is a fault in the assistant; please start the change again.",
        "uncertain": "The CRM's answer was lost, so I don't know yet whether the change was saved. "
                     "Reply \"yes\" to ask the CRM (it can never save this change twice), "
                     "or \"no\" to stop.",
        "blocked_wait": "First I need to settle the earlier change, whose answer was lost. "
                        "Reply \"yes\" to ask the CRM, or \"no\" to stop.",
        "still_unknown": "Still no answer from the CRM, so it is still unknown whether the change was saved. "
                         "Reply \"yes\" to ask again, or \"no\" to stop.",
        "confirmed": "Checked: the CRM confirms the change was saved. It now shows {label}: {value}.",
        "confirmed_unchanged": "Checked: the CRM already had {label}: {value}, so nothing needed saving.",
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
        "open_link": "Open in the CRM",
        "address_parts": "{address}, apartment {number}, floor {floor}",
        "place": "{address}, apartment {number}",
        "code": {
            "expected_option": "{label}: choose one of the listed values.",
            "expected_list": "{label}: choose one or more of the listed values.",
            "value_not_allowed": "{label}: this value is not one of the allowed values.",
            "expected_text": "{label}: this field takes text.",
            "expected_number": "{label}: this field takes a number (for example 12 or 3.5).",
            "expected_whole_number": "{label}: this field takes a whole number.",
            "expected_date": "{label}: this field takes a date like 2026-10-29.",
            "expected_yes_no": "{label}: this field takes yes or no.",
            "invalid_format": "{label}: the format is not valid.",
            "required": "{label}: this field can't be left empty.",
            "too_long": "{label}: the value is too long (at most {max_length} characters).",
            "invalid_phone": "{label}: this is not a valid phone number.",
            "phone_taken": "{label}: another contact already has this phone number.",
            "invalid_email": "{label}: this is not a valid email address.",
            "other": "{label}: the value was refused ({code}).",
        },
    },
    "he": {
        "start": "איזו דירה? כתבו את הכתובת (רחוב ומספר). אפשר גם לכתוב מספר דירה או שם דייר.",
        "no_match": "לא מצאתי דירה עבור \"{query}\" שאתם יכולים לראות. נסו כתובת אחרת.",
        "choose_apartment": "מצאתי כמה דירות. איזו מהן?",
        "truncated": "(מוצגות רק {count} הראשונות - כתבו כתובת מדויקת יותר כדי לצמצם.)",
        "pick_number": "אנא בחרו אחד מהמספרים שמוצגים.",
        "field_unclear": "לא הצלחתי לדעת איזה שדה הוא \"{words}\" - בחרו אותו מהרשימה.",
        "value_unclear": "לא הצלחתי להשתמש ב-\"{value}\" בתור {label} - בחרו או כתבו את הערך.",
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
        "confirm": "שינוי {label} ב{place}:\nמ: {old}\nל: {new}\n\n"
                   "השיבו \"כן\" כדי לשמור את השינוי, או \"לא\" כדי להשאיר את הערך הנוכחי.",
        "confirm_wait": "השיבו \"כן\" כדי לשמור בדיוק את השינוי הזה, או \"לא\" כדי להשאיר את הערך הנוכחי.",
        "kept": "לא שונה - {label} נשאר {old}.",
        "saved": "נשמר. ב-CRM מופיע עכשיו {label}: {value}.",
        "saved_hidden": "נשמר. אחרי השינוי ה-CRM כבר לא מאפשר לכם לערוך את {label}, ולכן הערך לא מוצג כאן.",
        "unchanged": "לא שונה דבר - ב-CRM כבר היה {label}: {value}.",
        "next": "בחרו שדה נוסף לשינוי, או לחצו על ביטול כדי לחפש משהו אחר.",
        "stale": "מישהו אחר שינה את הדירה בינתיים, ולכן לא נשמר דבר. המצב הנוכחי:",
        "not_found_reread": "הגרסה הזו של הדירה כבר אינה עדכנית, ולכן לא נשמר דבר. המצב הנוכחי:",
        "gone": "הדירה הזו כבר אינה זמינה לכם, ולכן לא נשמר דבר.",
        "read_forbidden": "ה-CRM לא אישר את הבקשה. לא שונה דבר.",
        "invalid": "ה-CRM לא קיבל את הערך. לא נשמר דבר.\n{reason}",
        "forbidden": "ה-CRM מדווח שאי אפשר לערוך כאן את {label}. לא נשמר דבר. טענתי את הדירה מחדש:",
        "reused": "ה-CRM דחה את הבקשה כי מזהה הבקשה כבר שימש לשינוי אחר. לא נשמר דבר. "
                  "זו תקלה בעוזר; אנא התחילו את השינוי מחדש.",
        "uncertain": "התשובה מה-CRM אבדה, ולכן עדיין לא ידוע אם השינוי נשמר. "
                     "השיבו \"כן\" כדי לשאול את ה-CRM (הוא לעולם לא ישמור את השינוי הזה פעמיים), "
                     "או \"לא\" כדי להפסיק.",
        "blocked_wait": "קודם צריך לברר את השינוי הקודם, שהתשובה עליו אבדה. "
                        "השיבו \"כן\" כדי לשאול את ה-CRM, או \"לא\" כדי להפסיק.",
        "still_unknown": "עדיין אין תשובה מה-CRM, ולכן עדיין לא ידוע אם השינוי נשמר. "
                         "השיבו \"כן\" כדי לשאול שוב, או \"לא\" כדי להפסיק.",
        "confirmed": "נבדק: ה-CRM מאשר שהשינוי נשמר. מופיע עכשיו {label}: {value}.",
        "confirmed_unchanged": "נבדק: ב-CRM כבר היה {label}: {value}, ולכן לא היה מה לשמור.",
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
        "open_link": "פתיחה ב-CRM",
        "address_parts": "{address}, דירה {number}, קומה {floor}",
        "place": "{address}, דירה {number}",
        "code": {
            "expected_option": "{label}: בחרו אחד מהערכים ברשימה.",
            "expected_list": "{label}: בחרו ערך אחד או יותר מהרשימה.",
            "value_not_allowed": "{label}: הערך הזה אינו אחד מהערכים המותרים.",
            "expected_text": "{label}: השדה מקבל טקסט.",
            "expected_number": "{label}: השדה מקבל מספר (למשל 12 או 3.5).",
            "expected_whole_number": "{label}: השדה מקבל מספר שלם.",
            "expected_date": "{label}: השדה מקבל תאריך כמו 2026-10-29.",
            "expected_yes_no": "{label}: השדה מקבל כן או לא.",
            "invalid_format": "{label}: הפורמט אינו תקין.",
            "required": "{label}: אי אפשר להשאיר את השדה הזה ריק.",
            "too_long": "{label}: הערך ארוך מדי (עד {max_length} תווים).",
            "invalid_phone": "{label}: זה אינו מספר טלפון תקין.",
            "phone_taken": "{label}: למספר הטלפון הזה כבר יש איש קשר אחר.",
            "invalid_email": "{label}: זו אינה כתובת אימייל תקינה.",
            "other": "{label}: הערך נדחה ({code}).",
        },
    },
}


def say(language: str, key: str, **values) -> str:
    return MESSAGES[language][key].format(**values)


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def new_request_id() -> str:
    return str(uuid.uuid4())


# --- display -----------------------------------------------------------------

def full_address(lang: str, apt: Apartment) -> str:
    empty = MESSAGES[lang]["empty"]
    return say(lang, "address_parts", address=apt.building_address or empty,
               number=apt.apartment_name or empty, floor=apt.floor or empty)


def place(lang: str, apt: Apartment) -> str:
    """"<building_address>, apartment <apartment_name>" (fixes118 decision 8): never the internal id."""
    empty = MESSAGES[lang]["empty"]
    return say(lang, "place", address=apt.building_address or empty, number=apt.apartment_name or empty)


def field_label(field: EditableField) -> str:
    """The CRM's one label, in the user's language; the key only if the CRM sent none."""
    return field.label or field.field_key


def option_label(option: Option) -> str:
    return option.label or str(option.value)


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
            return option_label(option)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)  # also an option the admin has since turned off: shown as stored


def field_lines(lang: str, fields: list[EditableField]) -> list[str]:
    return [f"- {field_label(f)}: {value_text(lang, f, f.current_value)}" for f in fields]


def apartment_text(lang: str, apt: Apartment) -> str:
    head = full_address(lang, apt)
    if apt.resident_name:
        head += say(lang, "resident", name=apt.resident_name)
    return "\n".join([head, *field_lines(lang, apt.fields)])


def link(lang: str, web_path) -> Optional[dict]:
    """The "Open in the CRM" link: CRM_WEB_URL + the CRM's own web_path, or None."""
    url = crm_api.web_link(web_path)
    return {"label": MESSAGES[lang]["open_link"], "url": url} if url else None


def numbered(items: list[str]) -> list[str]:
    return [f"{i}. {text}" for i, text in enumerate(items, 1)]


def fingerprint(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _fields_fingerprint(apt: Apartment) -> str:
    return fingerprint([apt.apartment_id, [[f.field_key, f.kind] for f in apt.fields]])


def _field_fingerprint(apt: Apartment, field: EditableField) -> str:
    return fingerprint([apt.apartment_id, field.field_key, field.kind, [o.value for o in field.options]])


def invalid_reasons(lang: str, body: dict, label: str) -> str:
    """The CRM's 422 as one line per refused value, naming the field."""
    codes = MESSAGES[lang]["code"]
    reasons = []
    for item in body.get("invalid_fields") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "invalid")
        reasons.append(codes.get(code, codes["other"]).format(
            label=label, code=code, max_length=item.get("max_length", "?")))
    return "\n".join(reasons) or codes["other"].format(label=label, code="invalid")


def is_reused(body: dict) -> bool:
    """A 422 `request_id_reused`: the same request_id came with a different request."""
    return body.get("status") == "invalid" and any(
        isinstance(i, dict) and i.get("code") == "request_id_reused" for i in body.get("invalid_fields") or [])


# --- one turn ------------------------------------------------------------------

def _answer(state: ApartmentEditState, outcome: str, prompt: str, *, intro: str = "", items=None,
            choices=None, multi: bool = False, clear=None, logout: bool = False,
            done: bool = False, web: Optional[dict] = None) -> dict:
    """`reply` is the whole text (a numbered list included, for any text channel); `prompt` is the
    same without the list, for the page, which shows `choices` as buttons instead."""
    lines = items or []
    head = (intro + "\n\n" if intro else "") + prompt
    answer = {
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
    if web:
        answer["link"] = web
    return answer


def start(language: str) -> dict:
    """A fresh conversation that asks for the address, nothing sent anywhere. The page now
    opens apartments from the combined search instead (open_apartment)."""
    state = ApartmentEditState(language=language)
    return _answer(state, "started", say(language, "start"))


def handle_turn(token: str, message: str, state: ApartmentEditState) -> dict:
    text = message.strip()
    lang = state.language

    if state.step == "blocked":
        if is_explicit_yes(text):
            return _send(token, state)
        if is_explicit_no(text):
            return _answer(ApartmentEditState(language=lang), "stopped", say(lang, "stopped_blocked"), done=True)
        return _answer(state, "blocked", say(lang, "blocked_wait"))

    if state.step == "confirm":
        if is_explicit_yes(text):
            return _send(token, state)
        if is_explicit_no(text):
            pending = state.pending
            return _reload(token, state, say(lang, "kept", label=pending.label, old=pending.old_text))
        return _answer(state, "confirm", say(lang, "confirm_wait"), choices=yes_no_buttons(lang))

    if state.step == "address":
        if is_explicit_no(text):
            return _cancel(state)
        return _search(token, state, text)

    if state.step == "choose_apartment":
        index = pick_index(text, len(state.candidates))
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
        field = pick_field(text, apt.fields)
        if field is None:
            if is_explicit_no(text):
                return _cancel(state)
            return _ask_field(state, apt, prefix=say(lang, "pick_number"))
        return _ask_value(state, apt, field)

    # choose_value
    field = next((f for f in apt.fields if f.field_key == state.field_key), None)
    if field is None or state.shown != _field_fingerprint(apt, field):
        return _ask_field(state, apt, intro=say(lang, "list_changed") + "\n" + apartment_text(lang, apt))
    understood, value, problem = pick_value(text, field, field.kind in CLEARABLE)
    if not understood:
        if is_explicit_no(text):
            return _cancel(state)
        return _ask_value(state, apt, field, prefix=say(lang, problem))
    return _confirm_screen(state, apt, field, value)


def _cancel(state: ApartmentEditState) -> dict:
    lang = state.language
    return _answer(ApartmentEditState(language=lang), "cancelled", say(lang, "cancelled"), done=True)


def yes_no_buttons(lang: str) -> list[dict]:
    return [{"label": MESSAGES[lang]["yes"], "message": "yes" if lang == "en" else "כן"},
            {"label": MESSAGES[lang]["no"], "message": "no" if lang == "en" else "לא"}]


# --- picking -------------------------------------------------------------------

def pick_index(text: str, count: int) -> Optional[int]:
    if text.isdigit() and 1 <= int(text) <= count:
        return int(text) - 1
    return None


def _matches(text: str, *labels) -> bool:
    wanted = _normalized(text)
    return any(label is not None and _normalized(str(label)) == wanted for label in labels)


def pick_field(text: str, fields: list[EditableField]) -> Optional[EditableField]:
    index = pick_index(text, len(fields))
    if index is not None:
        return fields[index]
    return next((f for f in fields if _matches(text, f.label)), None)


def _pick_option(token: str, options: list[Option]):
    """An option's value (as the CRM gave it, str or int), or None."""
    index = pick_index(token, len(options))
    if index is not None:
        return options[index].value
    for option in options:
        if _matches(token, option.value, option.label):
            return option.value
    return None


def pick_value(text: str, field: EditableField, clearable: bool) -> tuple[bool, Value, str]:
    """(understood, value to send, message key if not understood). None is a real value: it clears.
    `clearable`: whether the CRM lets this field be emptied (the caller knows its own rules)."""
    kind = field.kind
    clear = text == CLEAR_MESSAGE
    if clear:
        if not clearable:
            return False, None, "cannot_clear"
        return True, ([] if kind == "multi_option" else None), ""
    if kind == "single_option":
        value = _pick_option(text, field.options)
        return (False, None, "pick_number") if value is None else (True, value, "")
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
        return typed_date(text)
    return True, text, ""


def typed_date(text: str) -> tuple[bool, Value, str]:
    """A day-first date as YYYY-MM-DD; an impossible one asked again; anything else as typed."""
    day_first = DAY_FIRST_DATE.fullmatch(text)
    if day_first:
        day, month, year = map(int, day_first.groups())
        try:
            return True, datetime.date(year, month, day).isoformat(), ""
        except ValueError:
            return False, None, "bad_date"
    return True, text, ""


# --- the user's own words, from an understood request (docs/instructions21.md) ------------
# The interpreter never names CRM fields: these match the user's words against the labels and
# options the CRM sent. Nothing here keeps a field name; a doubtful match is asked about.

def _forms(text: str) -> set[str]:
    """The words, and without a joined Hebrew "ה" / "ל" ("הטלפון" -> "טלפון", "לאנגלית" -> "אנגלית")."""
    words = _normalized(text).strip(" .,:;!?\"'")
    forms = {words} if words else set()
    if re.match(r"^[הל]-?\w{2,}", words):
        forms.add(words[1:].lstrip("-"))
    return forms


def _whole(part: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(part) + r"(?!\w)", text) is not None


def match_words(words: str, labels: list[str]) -> list[int]:
    """Indexes of the labels the words name: exact matches (also without a joined ה/ל) win;
    otherwise every label found as whole words inside the words, or the words inside the label."""
    forms = _forms(words)
    labels = [_normalized(str(label)) for label in labels]
    exact = [i for i, label in enumerate(labels) if label in forms]
    if exact:
        return exact
    return [i for i, label in enumerate(labels) if label and any(_whole(label, f) or _whole(f, label) for f in forms)]


def understood_value(words: str, field: EditableField) -> tuple[bool, Value, str]:
    """A value the user wrote in a sentence -> (understood, value, message key). Stricter than
    pick_value: no list numbers (a "3" in a sentence is not option 3), never a clear, an option
    only when exactly one matches, a date only when it is a real day."""
    kind = field.kind
    if re.fullmatch(r"\[.*\]", words.strip()):
        return False, None, "value_unclear"  # a button's code ([clear], [skip] ...) is never a typed value
    if kind == "single_option":
        hits = match_words(words, [option_label(o) for o in field.options])
        return (True, field.options[hits[0]].value, "") if len(hits) == 1 else (False, None, "value_unclear")
    if kind == "yes_no":
        understood, value, _ = pick_value(words, field, clearable=False)
        return (understood and words.strip() not in ("1", "2"), value, "value_unclear")
    if kind == "date":
        understood, value, _ = typed_date(words.strip())
        try:
            datetime.date.fromisoformat(str(value))
            return understood, value, ""
        except ValueError:
            return False, None, "value_unclear"
    if kind == "number":
        number = words.strip()
        return (True, number, "") if re.fullmatch(r"-?\d+(?:[.,]\d+)?", number) else (False, None, "value_unclear")
    if kind == "text" and words.strip():
        return True, words.strip(), ""
    return False, None, "value_unclear"  # multi_option: chosen from the list


def plan_edit(lang: str, fields: list[EditableField], field_words: Optional[str],
              value_words: Optional[str]) -> tuple[Optional[EditableField], bool, Value, str]:
    """-> (the one field the words name or None, whether the value is understood, the value,
    a line to show first). The caller asks for the field / the value, or shows the confirm screen;
    nothing is ever saved without the explicit yes there."""
    if not field_words:
        return None, False, None, ""
    hits = match_words(field_words, [field_label(f) for f in fields])
    if len(hits) != 1:
        return None, False, None, say(lang, "field_unclear", words=field_words)
    field = fields[hits[0]]
    if not value_words:
        return field, False, None, ""
    understood, value, _ = understood_value(value_words, field)
    if not understood:
        return field, False, None, say(lang, "value_unclear", value=value_words, label=field_label(field))
    return field, True, value, ""


def value_question(lang: str, field: EditableField, current: str, clearable: bool) -> tuple[str, list, Optional[list],
                                                                                            Optional[dict]]:
    """(question, numbered items, buttons or None for the default ones, the clear button or None)."""
    kind = field.kind
    key = {"single_option": "choose_single", "multi_option": "choose_multi", "yes_no": "choose_yes_no",
           "text": "type_text", "number": "type_number", "date": "type_date"}[kind]
    question = say(lang, key, label=field_label(field), current=current)
    items, choices = [], None
    if kind in ("single_option", "multi_option"):
        items = numbered([option_label(o) for o in field.options])
    elif kind == "yes_no":
        items = numbered([MESSAGES[lang]["yes"], MESSAGES[lang]["no"]])
    clear = {"label": MESSAGES[lang]["clear"], "message": CLEAR_MESSAGE} if clearable else None
    if clear and kind != "multi_option":
        choices = [{"label": line, "message": str(i)} for i, line in enumerate(items, 1)]
        choices.append(clear)
    return question, items, choices, clear


# --- asking --------------------------------------------------------------------

def _ask_apartment(state: ApartmentEditState, prefix: str = "") -> dict:
    lang = state.language
    prompt = (prefix + "\n" if prefix else "") + say(lang, "choose_apartment")
    items = numbered([c.text for c in state.candidates])
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
    web = link(lang, apt.web_path)
    if not apt.fields:
        key = "no_fields_left" if after_change else "no_fields"
        shown = intro or apartment_text(lang, apt)
        return _answer(ApartmentEditState(language=lang), "no_fields", say(lang, key), intro=shown, done=True,
                       web=web)
    state.shown = _fields_fingerprint(apt)
    prompt = (prefix + "\n" if prefix else "") + say(lang, "choose_field")
    return _answer(state, "choose_field", prompt, intro=intro, items=numbered([field_label(f) for f in apt.fields]),
                   web=web)


def _ask_value(state: ApartmentEditState, apt: Apartment, field: EditableField, prefix: str = "",
               intro: str = "") -> dict:
    lang = state.language
    state.step = "choose_value"
    state.apartment_id, state.lineage_id = apt.apartment_id, apt.lineage_id
    state.field_key = field.field_key
    state.pending = None
    state.shown = _field_fingerprint(apt, field)
    question, items, choices, clear = value_question(
        lang, field, value_text(lang, field, field.current_value), field.kind in CLEARABLE)
    prompt = (prefix + "\n" if prefix else "") + question
    return _answer(state, "choose_value", prompt, intro=intro, items=items, choices=choices,
                   multi=field.kind == "multi_option", clear=clear)


def _confirm_screen(state: ApartmentEditState, apt: Apartment, field: EditableField, value,
                    intro: str = "") -> dict:
    lang = state.language
    label = field_label(field)
    state.step = "confirm"
    state.pending = PendingChange(field_key=field.field_key, value=value, label=label,
                                  old_text=value_text(lang, field, field.current_value),
                                  new_text=value_text(lang, field, value))
    reply = say(lang, "confirm", label=label, place=place(lang, apt), old=state.pending.old_text,
                new=state.pending.new_text)
    return _answer(state, "confirm", reply, intro=intro, choices=yes_no_buttons(lang))


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
                  text=full_address(lang, a) + (say(lang, "resident", name=a.resident_name) if a.resident_name else ""))
        for a in results
    ]
    return _ask_apartment(state)


def _read_lineage(token: str, state: ApartmentEditState, lineage_id: int):
    """(apartment, None), (None, None) if gone, or (None, failure answer)."""
    result = crm_api.get_apartment(token, lineage_id)
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


def open_apartment(token: str, language: str, lineage_id: int, field_words: Optional[str] = None,
                   value_words: Optional[str] = None) -> dict:
    """An apartment picked from the combined search (docs/instructions20.md): read it fresh
    by its lineage id and ask which field to change. With the user's own words from an
    understood request (docs/instructions21.md): the field they named is chosen, and with a
    value the CRM supports the confirm screen is shown - still saved only after a yes."""
    return _open_lineage(token, ApartmentEditState(language=language), lineage_id, field_words, value_words)


def _open_lineage(token: str, state: ApartmentEditState, lineage_id: int, field_words: Optional[str] = None,
                  value_words: Optional[str] = None) -> dict:
    apt, failure = _read_lineage(token, state, lineage_id)
    if failure:
        return failure
    if apt is None:
        return _gone(state)
    intro = apartment_text(state.language, apt)
    field, understood, value, prefix = plan_edit(state.language, apt.fields, field_words, value_words)
    if field is None:
        return _ask_field(state, apt, prefix=prefix, intro=intro)
    if not understood:
        return _ask_value(state, apt, field, prefix=prefix, intro=intro)
    return _confirm_screen(state, apt, field, value, intro=intro)


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


def _send(token: str, state: ApartmentEditState) -> dict:
    """The update for an explicit yes: the first send of a confirmed change, a resend after
    "not sent", or the check after a lost answer. Each carries the change's one request_id
    and its one body, so the CRM can never apply it twice."""
    lang = state.language
    pending = state.pending
    checking = state.step == "blocked"
    if pending.request_id is None:
        pending.request_id = new_request_id()
        pending.apartment_id = state.apartment_id
    result = crm_api.update_apartment_field(token, pending.apartment_id, pending.field_key, pending.value,
                                            pending.request_id)
    body = result.body
    status = body.get("status")

    if status in ("updated", "unchanged"):
        apt = Apartment(**body["apartment"])  # what the CRM saved and read back
        field = next((f for f in apt.fields if f.field_key == pending.field_key), None)
        label = field_label(field) if field else pending.label
        if status == "updated" and (body.get("value") is None and field is None):
            line = say(lang, "saved_hidden", label=pending.label)
        else:
            shown = value_text(lang, field, body.get("value"))
            if checking:
                line = say(lang, "confirmed" if status == "updated" else "confirmed_unchanged",
                           label=label, value=shown)
            else:
                line = say(lang, "saved" if status == "updated" else "unchanged", label=label, value=shown)
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
        return _answer(state, "uncertain", say(lang, "still_unknown" if checking else "uncertain"))
    if status == "crm_unreachable":  # no connection: this send never left
        if checking:
            return _answer(state, "blocked", say(lang, "still_unknown"))
        return _answer(state, "not_sent", say(lang, "not_sent"), choices=yes_no_buttons(lang))
    if status == "invalid":
        if is_reused(body):
            return _reload(token, state, say(lang, "reused"))
        reply = say(lang, "invalid", reason=invalid_reasons(lang, body, pending.label))
        return _reload(token, state, reply, ask_value_for=pending.field_key)
    if status == "forbidden":
        # The CRM gives one refusal for every reason (hidden, locked, unknown, not assigned): never explained here.
        return _reload(token, state, say(lang, "forbidden", label=pending.label))
    return _failure(state, body)


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
