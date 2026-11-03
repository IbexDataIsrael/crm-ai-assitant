"""A person (CRM contact) in the chat: open, edit one field, record a visit
(sheepsidebay_crm docs/fixes117.md, fixes117-118-helper-brief.md).

Reached from the combined search (app/search_chat.py): a person's button opens
them here. Admin and staff only - the CRM answers 404 to everyone else, the
same as for a missing contact, and the route refuses other roles before
calling it.

Like the apartment edit (app/apartment_chat.py, whose pickers and wording
this reuses): stateless on the server, the page holds the state; every turn
re-reads the contact (GET /api/assistant/contacts/{id}), so the field list and
the visit form are always the CRM's current ones and a number typed for an
old list is never applied. Nothing here keeps a field name, label or option:
they all come from the CRM, in the user's language.

- Edit a field: choose_field -> choose_value -> confirm -> POST /update.
  Text, number and date can be cleared (the CRM refuses an empty first name
  itself, with `required`); a list or yes/no never.
- Record a visit: the questions are the CRM's `new_visit_fields`, in its
  order; all the yes/no fields (the mitzvot) are asked together as one list
  to tick. Every question but the date can be skipped, and "Finish" goes to
  the confirm screen at any point. ONE confirm screen lists every value, then
  ONE call saves the whole visit. The date is always sent (the CRM's default,
  today, unless another was typed). The interactions (`entries`: what happened,
  each a category from the CRM's list) are ticked like the yes/no fields and
  sent as [{category, note: ""}]. Order (a labelled choice): the date, then
  what was done (yes/no fields, interactions), then the rest in the CRM's
  order. A draft contact (`can_add_visit` false) is not offered a visit.

From an understood request (docs/instructions21.md, app/search_chat.py): the
user's own words are matched against the CRM's labels and options here
(apartment_chat.plan_edit / match_words) - "change Dana's phone to 050..." opens
on the confirm screen; "tefillin for John today" starts the visit with Tefillin
ticked and the date set, both shown on the confirm screen like any other value.
A doubtful field or value is asked about, showing the real choices.

Every write carries a request_id, made at the first yes and kept until the
CRM's answer settles it. A lost answer BLOCKS the flow; the next yes sends the
same request_id with the same body, which the CRM answers from its record
(`replayed`) and can never apply twice. No edit or delete of visits, ever.
"""

import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app import apartment_chat, crm_api
from app.apartment_chat import (CLEAR_MESSAGE, EditableField, Value, field_label, field_lines, fingerprint,
                                invalid_reasons, is_reused, link, match_words, numbered, pick_field, pick_index,
                                pick_value, plan_edit, typed_date, understood_value, value_question, value_text,
                                yes_no_buttons)
from app.contact_chat import is_explicit_no, is_explicit_yes

Language = Literal["en", "he"]
Step = Literal["menu", "choose_field", "choose_value", "confirm", "blocked",
               "visit", "visit_confirm", "visit_blocked"]
EDIT_KINDS = ("single_option", "text", "number", "date", "yes_no")
CLEARABLE = ("text", "number", "date")
VISIT_KINDS = ("date", "single_option", "text", "number", "yes_no", "entries")
# The step of the visit questions that asks every yes/no field at once.
YES_NO_STEP = "[yes_no]"
# Sent only by the page's buttons.
EDIT_MESSAGE = "[edit]"
VISIT_MESSAGE = "[visit]"
SKIP_MESSAGE = "[skip]"
NEXT_MESSAGE = "[next]"  # "Continue" after tapping the items of a pick-several question
FINISH_MESSAGE = "[finish]"
DEFAULT_MESSAGE = "[default]"
# A visit date written as these words is the CRM's default date (today).
TODAY_WORDS = ("today", "היום")


# A visit value: a field's value, or the interactions [{category, note}] (category = an option's value).
VisitValue = Union[Value, list[dict[StrictStr, Union[StrictStr, StrictInt]]]]


class VisitField(BaseModel):
    """One entry of the CRM's new_visit_fields, as the CRM sent it."""

    model_config = ConfigDict(extra="ignore")

    field_key: str
    label: Optional[str] = None
    kind: str
    options: list[apartment_chat.Option] = Field(default_factory=list)
    required: bool = False
    default: Any = None


class Contact(BaseModel):
    """The CRM's contact view (GET /api/assistant/contacts/{id})."""

    model_config = ConfigDict(extra="ignore")

    contact_id: int
    status: str
    status_label: Optional[str] = None
    name: Optional[str] = None
    last_name: Optional[str] = None
    web_path: Any = None
    editable_fields: list[EditableField] = Field(default_factory=list)
    can_add_visit: bool = False
    new_visit_fields: list[VisitField] = Field(default_factory=list)
    recent_visits: list[dict] = Field(default_factory=list)

    @property
    def fields(self) -> list[EditableField]:
        return [f for f in self.editable_fields if f.kind in EDIT_KINDS]

    @property
    def visit_fields(self) -> list[VisitField]:
        return [f for f in self.new_visit_fields if f.kind in VISIT_KINDS]


class PendingEdit(BaseModel):
    """The field change on the confirm screen, or the one whose answer was lost."""

    model_config = ConfigDict(extra="forbid")

    field_key: str
    value: Value = None
    label: str = ""
    old_text: str = ""
    new_text: str = ""
    request_id: Optional[str] = None


class VisitDraft(BaseModel):
    """The visit being put together: the answers so far, nothing of the CRM's catalog."""

    model_config = ConfigDict(extra="forbid")

    position: int = 0
    values: dict[str, VisitValue] = Field(default_factory=dict)
    # A fingerprint of the visit form the questions were built from.
    shown: Optional[str] = None
    # Once sent: the exact body's values and its request_id, resent unchanged after a lost answer.
    sent: Optional[dict[str, VisitValue]] = None
    request_id: Optional[str] = None
    # Answers taken from the user's own request: their questions are not asked again.
    prefilled: list[str] = Field(default_factory=list)


class PersonState(BaseModel):
    """Held by the chat page between turns. No token, no field list."""

    model_config = ConfigDict(extra="forbid")

    step: Step = "menu"
    language: Language = "en"
    contact_id: Optional[int] = None
    field_key: Optional[str] = None
    shown: Optional[str] = None
    pending: Optional[PendingEdit] = None
    visit: Optional[VisitDraft] = None


MESSAGES = {
    "en": {
        "menu": "What do you want to do?",
        "edit": "Edit a field",
        "record_visit": "Record a visit",
        "draft_no_visit": "Visits can be recorded once this contact is approved.",
        "recent_visits": "Recent visits:",
        "no_visits": "No visits recorded yet.",
        "visit_line": "{date}{type}{done}",
        "pick_menu": "Please choose one of the buttons.",
        "no_fields": "Nothing about this person can be edited by you here.",
        "list_changed": "The details changed in the CRM since they were shown, so I didn't use that answer. "
                        "Here is the current state:",
        "confirm": "Change {label} of {name}:\nfrom: {old}\nto: {new}\n\n"
                   "Reply \"yes\" to save this change, or \"no\" to keep the current value.",
        "kept": "Not changed - {label} stays {old}.",
        "saved": "Saved. The CRM now shows {label}: {value}.",
        "saved_hidden": "Saved. After this change the CRM no longer lists {label} for you, so its value isn't shown.",
        "unchanged": "Nothing changed - the CRM already had {label}: {value}.",
        "forbidden": "The CRM did not allow this request. Nothing was changed.",
        "gone": "This person is no longer available to you in the chat, so nothing was saved.",
        "stopped_blocked": "Stopped. Please check this person in the CRM before changing them again.",
        "visit_start": "Recording a visit for {name}. Nothing is saved until you confirm at the end.",
        "visit_date": "{label} - choose \"Today ({default})\", or type another date day first "
                      "(for example 29/10/2026):",
        "visit_date_no_default": "{label} - type the date day first (for example 29/10/2026):",
        "today": "Today ({default})",
        "visit_single": "{label}:",
        "visit_text": "{label} - type it, or skip:",
        "visit_number": "{label} - type a number, or skip:",
        "visit_yes_no": "What was done? Tap each one that was done (tap again to remove it), then \"Continue\".",
        "continue": "Continue",
        "none_ticked": "None of these",
        "skip": "Skip",
        "finish": "Done - review the visit",
        "visit_entries": "{label} - what happened during the visit? Tap each one (tap again to remove it), "
                         "then \"Continue\".",
        "empty_fields": "({n} more fields are empty - choose \"Edit a field\" to fill them.)",
        "empty_field": "(1 more field is empty - choose \"Edit a field\" to fill it.)",
        "field_is_detail": "\"{words}\" is a detail of the person, not part of a visit, so here it is to change:",
        "field_is_visit": "\"{words}\" is recorded with a visit, so here is a new visit:",
        "visit_changed": "The visit form changed in the CRM meanwhile, so I started the questions again. "
                         "Nothing was saved.",
        "visit_confirm": "Record a visit for {name}:\n{lines}\n\nReply \"yes\" to save this visit, or \"no\" to cancel.",
        "visit_confirm_wait": "Reply \"yes\" to save exactly this visit, or \"no\" to cancel.",
        "done_mark": "✓",
        "visit_saved": "Saved. The CRM recorded the visit: {summary}.",
        "visit_saved_plain": "Saved. The CRM recorded the visit.",
        "visit_cancelled": "The visit was not recorded.",
        "approve_first": "The CRM records visits only for an approved contact. Nothing was saved.",
        "visit_refused": "The CRM did not accept the visit. Nothing was saved.\n{reason}",
        "visit_uncertain": "The CRM's answer was lost, so I don't know yet whether the visit was saved. "
                           "Reply \"yes\" to ask the CRM (it can never record this visit twice), or \"no\" to stop.",
        "visit_still_unknown": "Still no answer from the CRM, so it is still unknown whether the visit was saved. "
                               "Reply \"yes\" to ask again, or \"no\" to stop.",
        "visit_blocked_wait": "First I need to settle the earlier visit, whose answer was lost. "
                              "Reply \"yes\" to ask the CRM, or \"no\" to stop.",
        "visit_not_sent": "I couldn't reach the CRM, so the visit was not sent. Reply \"yes\" to try again, "
                          "or \"no\" to cancel.",
        "from_message": "From your message: {items}.",
        "contacts_missing": "Opening a person in the chat isn't available on this CRM yet. Nothing was changed.",
    },
    "he": {
        "menu": "מה תרצו לעשות?",
        "edit": "עריכת שדה",
        "record_visit": "רישום ביקור",
        "draft_no_visit": "אפשר לרשום ביקורים אחרי שאיש הקשר יאושר.",
        "recent_visits": "ביקורים אחרונים:",
        "no_visits": "עדיין לא נרשמו ביקורים.",
        "visit_line": "{date}{type}{done}",
        "pick_menu": "אנא בחרו באחד הכפתורים.",
        "no_fields": "אין פרט של האדם הזה שאתם יכולים לערוך כאן.",
        "list_changed": "הפרטים השתנו ב-CRM מאז שהוצגו, ולכן לא השתמשתי בתשובה. זה המצב הנוכחי:",
        "confirm": "שינוי {label} של {name}:\nמ: {old}\nל: {new}\n\n"
                   "השיבו \"כן\" כדי לשמור את השינוי, או \"לא\" כדי להשאיר את הערך הנוכחי.",
        "kept": "לא שונה - {label} נשאר {old}.",
        "saved": "נשמר. ב-CRM מופיע עכשיו {label}: {value}.",
        "saved_hidden": "נשמר. אחרי השינוי ה-CRM כבר לא מציג לכם את {label}, ולכן הערך לא מוצג.",
        "unchanged": "לא שונה דבר - ב-CRM כבר היה {label}: {value}.",
        "forbidden": "ה-CRM לא אישר את הבקשה. לא שונה דבר.",
        "gone": "האדם הזה כבר אינו זמין לכם בצ'אט, ולכן לא נשמר דבר.",
        "stopped_blocked": "הופסק. אנא בדקו את האדם ב-CRM לפני שתשנו אותו שוב.",
        "visit_start": "רישום ביקור אצל {name}. שום דבר לא נשמר לפני האישור בסוף.",
        "visit_date": "{label} - בחרו \"היום ({default})\", או כתבו תאריך אחר, יום קודם (למשל 29/10/2026):",
        "visit_date_no_default": "{label} - כתבו את התאריך, יום קודם (למשל 29/10/2026):",
        "today": "היום ({default})",
        "visit_single": "{label}:",
        "visit_text": "{label} - כתבו, או דלגו:",
        "visit_number": "{label} - כתבו מספר, או דלגו:",
        "visit_yes_no": "מה נעשה? הקישו על כל מה שנעשה (הקשה נוספת מבטלת), ואז \"המשך\".",
        "continue": "המשך",
        "none_ticked": "אף אחד מאלה",
        "skip": "דילוג",
        "finish": "סיימתי - לסיכום הביקור",
        "visit_entries": "{label} - מה היה בביקור? הקישו על כל מה שהיה (הקשה נוספת מבטלת), ואז \"המשך\".",
        "empty_fields": "(עוד {n} שדות ריקים - בחרו \"עריכת שדה\" כדי למלא אותם.)",
        "empty_field": "(עוד שדה אחד ריק - בחרו \"עריכת שדה\" כדי למלא אותו.)",
        "field_is_detail": "\"{words}\" הוא פרט של האדם ולא חלק מביקור, אז הנה הוא לשינוי:",
        "field_is_visit": "\"{words}\" נרשם עם ביקור, אז הנה ביקור חדש:",
        "visit_changed": "טופס הביקור השתנה ב-CRM בינתיים, ולכן התחלתי את השאלות מחדש. לא נשמר דבר.",
        "visit_confirm": "רישום ביקור אצל {name}:\n{lines}\n\nהשיבו \"כן\" כדי לשמור את הביקור, או \"לא\" כדי לבטל.",
        "visit_confirm_wait": "השיבו \"כן\" כדי לשמור בדיוק את הביקור הזה, או \"לא\" כדי לבטל.",
        "done_mark": "✓",
        "visit_saved": "נשמר. ה-CRM רשם את הביקור: {summary}.",
        "visit_saved_plain": "נשמר. ה-CRM רשם את הביקור.",
        "visit_cancelled": "הביקור לא נרשם.",
        "approve_first": "ה-CRM רושם ביקורים רק לאיש קשר מאושר. לא נשמר דבר.",
        "visit_refused": "ה-CRM לא קיבל את הביקור. לא נשמר דבר.\n{reason}",
        "visit_uncertain": "התשובה מה-CRM אבדה, ולכן עדיין לא ידוע אם הביקור נשמר. "
                           "השיבו \"כן\" כדי לשאול את ה-CRM (הוא לעולם לא ירשום את הביקור הזה פעמיים), "
                           "או \"לא\" כדי להפסיק.",
        "visit_still_unknown": "עדיין אין תשובה מה-CRM, ולכן עדיין לא ידוע אם הביקור נשמר. "
                               "השיבו \"כן\" כדי לשאול שוב, או \"לא\" כדי להפסיק.",
        "visit_blocked_wait": "קודם צריך לברר את הביקור הקודם, שהתשובה עליו אבדה. "
                              "השיבו \"כן\" כדי לשאול את ה-CRM, או \"לא\" כדי להפסיק.",
        "visit_not_sent": "לא הצלחתי להגיע ל-CRM, ולכן הביקור לא נשלח. השיבו \"כן\" כדי לנסות שוב, או \"לא\" כדי לבטל.",
        "from_message": "מההודעה שלכם: {items}.",
        "contacts_missing": "פתיחת אדם בצ'אט עדיין אינה זמינה ב-CRM הזה. לא שונה דבר.",
    },
}


def say(language: str, key: str, **values) -> str:
    text = MESSAGES[language].get(key)
    if text is None:  # the apartment flow's shared wording (not sent, uncertain, codes ...)
        return apartment_chat.say(language, key, **values)
    return text.format(**values)


# --- display -------------------------------------------------------------------

def person_name(lang: str, contact: Contact) -> str:
    name = " ".join(p for p in (contact.name, contact.last_name) if p and p.strip())
    return name or apartment_chat.MESSAGES[lang]["empty"]


def visit_line(lang: str, visit: dict) -> str:
    done = visit.get("done") or []
    return "- " + say(lang, "visit_line", date=visit.get("visit_date") or "",
                      type=f" - {visit['visit_type']}" if visit.get("visit_type") else "",
                      done=": " + ", ".join(done) if done else "")


def contact_text(lang: str, contact: Contact) -> str:
    head = person_name(lang, contact)
    if contact.status_label:
        head += f" [{contact.status_label}]"
    filled = [f for f in contact.fields if not apartment_chat._is_empty(f.current_value)]
    lines = [head, *field_lines(lang, filled)]
    empty = len(contact.fields) - len(filled)
    if empty:
        lines.append(say(lang, "empty_field") if empty == 1 else say(lang, "empty_fields", n=empty))
    lines.append(say(lang, "recent_visits"))
    if contact.recent_visits:
        lines += [visit_line(lang, v) for v in contact.recent_visits]
    else:
        lines.append("- " + say(lang, "no_visits"))
    return "\n".join(lines)


def _fields_fingerprint(contact: Contact) -> str:
    return fingerprint([contact.contact_id, [[f.field_key, f.kind] for f in contact.fields]])


def _field_fingerprint(contact: Contact, field: EditableField) -> str:
    return fingerprint([contact.contact_id, field.field_key, field.kind, [o.value for o in field.options]])


def _visit_fingerprint(contact: Contact) -> str:
    return fingerprint([contact.contact_id, [[f.field_key, f.kind, [o.value for o in f.options]]
                                             for f in contact.visit_fields]])


def visit_steps(contact: Contact) -> list[str]:
    """The visit questions: the visit date first, then what was done (every yes/no field together
    as one step, the interactions), then the other fields in the CRM's order."""
    fields = contact.visit_fields
    head = [f.field_key for f in fields[:1] if f.kind == "date"]
    done = ([YES_NO_STEP] if any(f.kind == "yes_no" for f in fields) else []) + \
        [f.field_key for f in fields if f.kind == "entries"]
    rest = [f.field_key for f in fields if f.field_key not in head and f.kind not in ("yes_no", "entries")]
    return head + done + rest


# --- one turn ------------------------------------------------------------------

def _answer(state: PersonState, outcome: str, prompt: str, *, intro: str = "", items=None, choices=None,
            multi: bool = False, clear=None, logout: bool = False, done: bool = False,
            web: Optional[dict] = None) -> dict:
    """The apartment flow's answer shape: `reply` the whole text, `prompt` without the list,
    `choices` the buttons (each sends its `message` as the next turn)."""
    lines = items or []
    head = (intro + "\n\n" if intro else "") + prompt
    answer = {
        "intent": "person",
        "outcome": outcome,
        "reply": "\n".join([head, *lines]),
        "prompt": head,
        "state": state.model_dump(),
        "choices": choices if choices is not None else [
            {"label": line, "message": str(i)} for i, line in enumerate(lines, 1)],
        "multi": multi,
        "clear": clear,
        "awaiting_check": state.step in ("blocked", "visit_blocked"),
        "logout": logout,
        "show_menu": done,
    }
    if web:
        answer["link"] = web
    return answer


def open_person(token: str, language: str, contact_id: int, *, edit: bool = False, visit: bool = False,
                field_words: Optional[str] = None, value_words: Optional[str] = None,
                date_words: Optional[str] = None, notes: Optional[str] = None) -> dict:
    """A person picked from the combined search: read them fresh and offer what can be done.
    From an understood request: `edit` goes to the field the user named (and with a value the
    CRM supports, to the confirm screen); `visit` starts the visit questions with what the user
    already said filled in. Nothing is saved without the explicit yes on a confirm screen."""
    state = PersonState(language=language, contact_id=contact_id)
    contact, failure = _read(token, state)
    if failure:
        return failure
    intro = contact_text(language, contact)
    # Code decides with the CRM's own lists, whatever the model guessed: a named item that is
    # only a detail of the person is edited; one that only a visit records starts a visit.
    note = ""
    if field_words and contact.can_add_visit:
        in_visit = _names_visit_item(contact, field_words)
        in_person = plan_edit(language, contact.fields, field_words, None)[0] is not None
        if visit and in_person and not in_visit:
            visit, edit, note = False, True, say(language, "field_is_detail", words=field_words)
        elif edit and in_visit and not in_person:
            visit, edit, note = True, False, say(language, "field_is_visit", words=field_words)
    if visit and contact.can_add_visit:
        state.visit = VisitDraft(shown=_visit_fingerprint(contact))
        noted = _prefill_visit(language, contact, state.visit, field_words, value_words, date_words, notes)
        start = (note + "\n" if note else "") + say(language, "visit_start", name=person_name(language, contact))
        if noted:
            start += "\n" + say(language, "from_message", items=", ".join(noted))
        return _ask_visit(state, contact, intro=start)
    if not edit:
        return _menu(state, contact, intro=intro)  # also a visit for a draft: the menu says why not
    if note:
        intro += "\n\n" + note
    field, understood, value, prefix = plan_edit(language, contact.fields, field_words, value_words)
    if field is None:
        return _ask_field(state, contact, prefix=prefix, intro=intro)
    if not understood:
        return _ask_value(state, contact, field, prefix=prefix, intro=intro)
    return _confirm_screen(state, contact, field, value, intro=intro)


def _names_visit_item(contact: Contact, words: str) -> bool:
    """Whether the words name something a visit records: a yes/no visit field or an interaction category."""
    for f in contact.visit_fields:
        if f.kind == "yes_no" and match_words(words, [field_label(f)]):
            return True
        if f.kind == "entries" and match_words(words, [apartment_chat.option_label(o) for o in f.options]):
            return True
    return False


def _prefill_visit(lang: str, contact: Contact, visit: VisitDraft, field_words: Optional[str],
                   value_words: Optional[str], date_words: Optional[str], notes: Optional[str]) -> list[str]:
    """Fill the visit with what the user's request already says, matched against the CRM's own
    visit fields: a yes/no field it names is ticked; a date it gives is set; a named field with a
    value the CRM supports is set. -> the lines to show ("Tefillin ✓", "Date: 2026-11-03")."""
    noted = []
    said = [t for t in (field_words, notes) if t]
    for f in contact.visit_fields:
        label = field_label(f)
        value = None
        if f.kind == "yes_no":
            if any(match_words(t, [label]) for t in said):
                visit.values[f.field_key] = True
                noted.append(f"{label} {say(lang, 'done_mark')}")
            continue
        if f.kind == "entries":
            # Each category the user named is pre-ticked; the question is still shown to confirm or add.
            named = [o for o in f.options if any(match_words(t, [apartment_chat.option_label(o)]) for t in said)]
            if named:
                visit.values[f.field_key] = [{"category": o.value, "note": ""} for o in named]
                noted += [f"{apartment_chat.option_label(o)} {say(lang, 'done_mark')}" for o in named]
            continue
        if f.kind == "date" and date_words:
            if date_words.strip().casefold() in TODAY_WORDS and isinstance(f.default, str):
                value = f.default
            else:
                understood, typed, _ = understood_value(date_words, f)
                value = typed if understood else None
        elif field_words and value_words and match_words(field_words, [label]):
            understood, typed, _ = understood_value(value_words, f)
            value = typed if understood else None
        if value is not None:
            visit.values[f.field_key] = value
            visit.prefilled.append(f.field_key)
            noted.append(f"{label}: {value_text(lang, f, value)}")
    return noted


def handle_turn(token: str, message: str, state: PersonState) -> dict:
    text = message.strip()
    lang = state.language

    if state.step in ("blocked", "visit_blocked"):
        if is_explicit_yes(text):
            return _send_edit(token, state) if state.step == "blocked" else _send_visit(token, state)
        if is_explicit_no(text):
            return _answer(PersonState(language=lang), "stopped", say(lang, "stopped_blocked"), done=True)
        return _answer(state, "blocked", say(lang, "blocked_wait" if state.step == "blocked" else "visit_blocked_wait"))

    if state.step == "confirm":
        if is_explicit_yes(text):
            return _send_edit(token, state)
        if is_explicit_no(text):
            pending = state.pending
            return _reload(token, state, say(lang, "kept", label=pending.label, old=pending.old_text))
        return _answer(state, "confirm", say(lang, "confirm_wait"), choices=yes_no_buttons(lang))

    if state.step == "visit_confirm":
        if is_explicit_yes(text):
            return _send_visit(token, state)
        if is_explicit_no(text):
            return _reload(token, state, say(lang, "visit_cancelled"))
        return _answer(state, "visit_confirm", say(lang, "visit_confirm_wait"), choices=yes_no_buttons(lang))

    contact, failure = _read(token, state)
    if failure:
        return failure

    if state.step == "menu":
        if text == EDIT_MESSAGE or text == "1":
            return _ask_field(state, contact)
        if contact.can_add_visit and (text == VISIT_MESSAGE or text == "2"):
            return _start_visit(state, contact)
        if is_explicit_no(text):
            return _cancel(state)
        return _menu(state, contact, prefix=say(lang, "pick_menu"))

    if state.step == "visit":
        return _visit_turn(state, contact, text)

    if state.step == "choose_field":
        if state.shown != _fields_fingerprint(contact):
            return _ask_field(state, contact, intro=say(lang, "list_changed") + "\n" + contact_text(lang, contact))
        field = pick_field(text, contact.fields)
        if field is None:
            if is_explicit_no(text):
                return _cancel(state)
            return _ask_field(state, contact, prefix=say(lang, "pick_number"))
        return _ask_value(state, contact, field)

    # choose_value
    field = next((f for f in contact.fields if f.field_key == state.field_key), None)
    if field is None or state.shown != _field_fingerprint(contact, field):
        return _ask_field(state, contact, intro=say(lang, "list_changed") + "\n" + contact_text(lang, contact))
    understood, value, problem = pick_value(text, field, field.kind in CLEARABLE)
    if not understood:
        if is_explicit_no(text):
            return _cancel(state)
        return _ask_value(state, contact, field, prefix=say(lang, problem))
    return _confirm_screen(state, contact, field, value)


def _cancel(state: PersonState) -> dict:
    lang = state.language
    return _answer(PersonState(language=lang), "cancelled", say(lang, "cancelled"), done=True)


# --- the menu and the field edit ----------------------------------------------------

def _menu(state: PersonState, contact: Contact, prefix: str = "", intro: str = "") -> dict:
    lang = state.language
    state.step = "menu"
    state.field_key = state.shown = None
    state.pending = state.visit = None
    choices = [{"label": say(lang, "edit"), "message": EDIT_MESSAGE}]
    lines = [prefix] if prefix else []
    if contact.can_add_visit:
        choices.append({"label": say(lang, "record_visit"), "message": VISIT_MESSAGE})
    else:
        lines.append(say(lang, "draft_no_visit"))
    prompt = "\n".join([*lines, say(lang, "menu")])
    return _answer(state, "menu", prompt, intro=intro, choices=choices, web=link(lang, contact.web_path))


def _ask_field(state: PersonState, contact: Contact, prefix: str = "", intro: str = "") -> dict:
    lang = state.language
    if not contact.fields:
        return _menu(state, contact, prefix=say(lang, "no_fields"), intro=intro)
    state.step = "choose_field"
    state.field_key = None
    state.pending = None
    state.shown = _fields_fingerprint(contact)
    prompt = (prefix + "\n" if prefix else "") + say(lang, "choose_field")
    return _answer(state, "choose_field", prompt, intro=intro, items=numbered([field_label(f) for f in contact.fields]))


def _ask_value(state: PersonState, contact: Contact, field: EditableField, prefix: str = "",
               intro: str = "") -> dict:
    lang = state.language
    state.step = "choose_value"
    state.field_key = field.field_key
    state.pending = None
    state.shown = _field_fingerprint(contact, field)
    question, items, choices, clear = value_question(
        lang, field, value_text(lang, field, field.current_value), field.kind in CLEARABLE)
    prompt = (prefix + "\n" if prefix else "") + question
    return _answer(state, "choose_value", prompt, intro=intro, items=items, choices=choices, clear=clear)


def _confirm_screen(state: PersonState, contact: Contact, field: EditableField, value, intro: str = "") -> dict:
    lang = state.language
    label = field_label(field)
    state.step = "confirm"
    state.pending = PendingEdit(field_key=field.field_key, value=value, label=label,
                                old_text=value_text(lang, field, field.current_value),
                                new_text=value_text(lang, field, value))
    reply = say(lang, "confirm", label=label, name=person_name(lang, contact), old=state.pending.old_text,
                new=state.pending.new_text)
    return _answer(state, "confirm", reply, intro=intro, choices=yes_no_buttons(lang))


def _send_edit(token: str, state: PersonState) -> dict:
    lang = state.language
    pending = state.pending
    checking = state.step == "blocked"
    if pending.request_id is None:
        pending.request_id = apartment_chat.new_request_id()
    result = crm_api.update_contact_field(token, state.contact_id, pending.field_key, pending.value,
                                          pending.request_id)
    body = result.body
    status = body.get("status")

    if status in ("updated", "unchanged"):
        contact = Contact(**body["contact"])  # what the CRM saved and read back
        field = next((f for f in contact.fields if f.field_key == pending.field_key), None)
        label = field_label(field) if field else pending.label
        if status == "updated" and body.get("value") is None and field is None:
            line = say(lang, "saved_hidden", label=pending.label)
        else:
            shown = value_text(lang, field, body.get("value"))
            if checking:
                key = "confirmed" if status == "updated" else "confirmed_unchanged"
            else:
                key = "saved" if status == "updated" else "unchanged"
            line = say(lang, key, label=label, value=shown)
        answer = _menu(state, contact, intro=line + "\n" + contact_text(lang, contact))
        answer["outcome"] = status
        return answer
    if status == "not_found":
        return _gone(state)
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
        # One fixed message for every reason (hidden, unknown, not allowed): never explained here.
        return _reload(token, state, say(lang, "forbidden"))
    return _failure(state, body)


# --- recording a visit ----------------------------------------------------------

def _start_visit(state: PersonState, contact: Contact, intro: str = "") -> dict:
    lang = state.language
    state.visit = VisitDraft(shown=_visit_fingerprint(contact))
    start = say(lang, "visit_start", name=person_name(lang, contact))
    return _ask_visit(state, contact, intro=(intro + "\n\n" if intro else "") + start)


def _visit_field(contact: Contact, key: str) -> Optional[VisitField]:
    return next((f for f in contact.visit_fields if f.field_key == key), None)


def _ask_visit(state: PersonState, contact: Contact, prefix: str = "", intro: str = "") -> dict:
    """The current visit question, or the confirm screen once every question was asked."""
    lang = state.language
    state.step = "visit"
    steps = visit_steps(contact)
    while state.visit.position < len(steps) and steps[state.visit.position] in state.visit.prefilled:
        state.visit.position += 1  # already answered by the user's own request
    if state.visit.position >= len(steps):
        return _visit_confirm(state, contact, intro=intro)
    step = steps[state.visit.position]
    skip = {"label": say(lang, "skip"), "message": SKIP_MESSAGE}
    finish = {"label": say(lang, "finish"), "message": FINISH_MESSAGE}
    pre = prefix + "\n" if prefix else ""
    if _is_several(contact, step):
        return _ask_several(state, contact, step, pre, intro)
    field = _visit_field(contact, step)
    label = field_label(field)
    if field.kind == "date":
        # The first question: no "done" button here (it would save a visit with nothing but a date).
        default = field.default if isinstance(field.default, str) else None
        if default:
            question = say(lang, "visit_date", label=label, default=default)
            choices = [{"label": say(lang, "today", default=default), "message": DEFAULT_MESSAGE}]
        else:
            question = say(lang, "visit_date_no_default", label=label)
            choices = [skip] if not field.required else []
        return _answer(state, "visit", pre + question, intro=intro, choices=choices)
    if field.kind == "single_option":
        items = numbered([apartment_chat.option_label(o) for o in field.options])
        choices = [{"label": line, "message": str(i)} for i, line in enumerate(items, 1)] + [skip, finish]
        return _answer(state, "visit", pre + say(lang, "visit_single", label=label), intro=intro, items=items,
                       choices=choices)
    key = "visit_number" if field.kind == "number" else "visit_text"
    return _answer(state, "visit", pre + say(lang, key, label=label), intro=intro, choices=[skip, finish])


def _visit_turn(state: PersonState, contact: Contact, text: str) -> dict:
    lang = state.language
    visit = state.visit
    if visit is None or visit.shown != _visit_fingerprint(contact) or not contact.can_add_visit:
        if not contact.can_add_visit:
            return _menu(state, contact, intro=contact_text(lang, contact))
        return _start_visit(state, contact, intro=say(lang, "visit_changed"))
    steps = visit_steps(contact)
    step = steps[visit.position] if visit.position < len(steps) else None
    if text == FINISH_MESSAGE:
        return _visit_confirm(state, contact)
    if text == SKIP_MESSAGE and step is not None and not _required(contact, step):
        if step == YES_NO_STEP:
            for f in contact.visit_fields:
                if f.kind == "yes_no":
                    visit.values.pop(f.field_key, None)
        else:
            visit.values.pop(step, None)
        visit.position += 1
        return _ask_visit(state, contact)
    if step is None:
        return _visit_confirm(state, contact)
    # No word cancels here ("no" may be a value): the page's Cancel button does.
    if _is_several(contact, step):
        labels, ticked = _several(contact, step, visit)
        if text == NEXT_MESSAGE:
            visit.position += 1
            return _ask_visit(state, contact)
        parts = [p.strip() for p in text.replace("،", ",").split(",") if p.strip()]
        indexes = [pick_index(p, len(labels)) for p in parts]
        if not indexes or None in indexes:
            return _ask_visit(state, contact, prefix=say(lang, "pick_number"))
        if len(indexes) == 1:
            # A tap on one item's button: tick it, or untick it; the same question comes back.
            ticked[indexes[0]] = not ticked[indexes[0]]
            _set_several(contact, step, visit, ticked)
            return _ask_visit(state, contact)
        # "1,3" typed (a text channel): exactly these, then the next question.
        _set_several(contact, step, visit, [i in indexes for i in range(len(labels))])
        visit.position += 1
        return _ask_visit(state, contact)
    field = _visit_field(contact, step)
    if field.kind == "date":
        if text == DEFAULT_MESSAGE and isinstance(field.default, str):
            visit.values[field.field_key] = field.default
        else:
            understood, value, problem = typed_date(text)
            if not understood or not _is_iso_date(value):
                return _ask_visit(state, contact, prefix=say(lang, problem or "bad_date"))
            visit.values[field.field_key] = value
    elif field.kind == "single_option":
        understood, value, problem = pick_value(text, field, clearable=False)
        if not understood:
            return _ask_visit(state, contact, prefix=say(lang, problem))
        visit.values[field.field_key] = value
    else:  # text, number: as typed; the CRM checks it
        if not text or text.startswith("[") and text.endswith("]"):
            return _ask_visit(state, contact, prefix=say(lang, "not_understood_value"))
        visit.values[field.field_key] = text
    visit.position += 1
    return _ask_visit(state, contact)


def _is_several(contact: Contact, step: str) -> bool:
    """A pick-several question: every yes/no field together, or the interactions."""
    if step == YES_NO_STEP:
        return True
    field = _visit_field(contact, step)
    return field is not None and field.kind == "entries"


def _several(contact: Contact, step: str, visit: VisitDraft) -> tuple[list[str], list[bool]]:
    """(the items' labels, which are ticked) of a pick-several question."""
    if step == YES_NO_STEP:
        boxes = [f for f in contact.visit_fields if f.kind == "yes_no"]
        return [field_label(f) for f in boxes], [visit.values.get(f.field_key) is True for f in boxes]
    field = _visit_field(contact, step)
    picked = {e.get("category") for e in visit.values.get(field.field_key) or [] if isinstance(e, dict)}
    return [apartment_chat.option_label(o) for o in field.options], [o.value in picked for o in field.options]


def _set_several(contact: Contact, step: str, visit: VisitDraft, ticked: list[bool]) -> None:
    if step == YES_NO_STEP:
        boxes = [f for f in contact.visit_fields if f.kind == "yes_no"]
        for f, on in zip(boxes, ticked):
            if on:
                visit.values[f.field_key] = True
            else:
                visit.values.pop(f.field_key, None)  # an unticked mitzva is simply left out
        return
    field = _visit_field(contact, step)
    visit.values[field.field_key] = [{"category": o.value, "note": ""} for o, on in zip(field.options, ticked) if on]


def _ask_several(state: PersonState, contact: Contact, step: str, pre: str, intro: str) -> dict:
    """Bot-style: every item is a button; a tap ticks or unticks it (the question comes back with
    "✓" on the ticked ones), and "Continue" moves on ("None of these" while nothing is ticked).
    The text lists the items numbered, so "1,3" can be typed on a text-only channel."""
    lang = state.language
    labels, ticked = _several(contact, step, state.visit)
    mark = say(lang, "done_mark")
    items = [f"{i}. {label}" + (f" {mark}" if on else "") for i, (label, on) in enumerate(zip(labels, ticked), 1)]
    choices = [{"label": (f"{mark} " if on else "") + label, "message": str(i)}
               for i, (label, on) in enumerate(zip(labels, ticked), 1)]
    choices.append({"label": say(lang, "continue" if any(ticked) else "none_ticked"), "message": NEXT_MESSAGE})
    if step == YES_NO_STEP:
        question = say(lang, "visit_yes_no")
    else:
        question = say(lang, "visit_entries", label=field_label(_visit_field(contact, step)))
    return _answer(state, "visit", pre + question, intro=intro, items=items, choices=choices)


def _required(contact: Contact, step: str) -> bool:
    field = _visit_field(contact, step) if step != YES_NO_STEP else None
    return bool(field and field.required and not isinstance(field.default, str))


def _is_iso_date(value) -> bool:
    try:
        datetime.date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def _visit_values(contact: Contact, visit: VisitDraft) -> dict:
    """What is sent: the answers in the CRM's order; the date always (its default unless typed)."""
    values = {}
    for f in contact.visit_fields:
        if f.field_key in visit.values:
            if f.kind == "entries" and not visit.values[f.field_key]:
                continue  # nothing ticked: no interactions are sent
            values[f.field_key] = visit.values[f.field_key]
        elif f.kind == "date" and isinstance(f.default, str):
            values[f.field_key] = f.default
    return values


def _visit_confirm(state: PersonState, contact: Contact, intro: str = "") -> dict:
    lang = state.language
    state.step = "visit_confirm"
    values = _visit_values(contact, state.visit)
    lines = []
    for f in contact.visit_fields:
        if f.field_key not in values:
            continue
        if f.kind == "yes_no":
            lines.append(f"- {field_label(f)} {say(lang, 'done_mark')}")
        elif f.kind == "entries":
            names = {o.value: apartment_chat.option_label(o) for o in f.options}
            lines.append(f"- {field_label(f)}: " + ", ".join(str(names.get(e["category"], e["category"]))
                                                            for e in values[f.field_key]))
        else:
            lines.append(f"- {field_label(f)}: {value_text(lang, f, values[f.field_key])}")
    reply = say(lang, "visit_confirm", name=person_name(lang, contact), lines="\n".join(lines))
    return _answer(state, "visit_confirm", reply, intro=intro, choices=yes_no_buttons(lang))


def _send_visit(token: str, state: PersonState) -> dict:
    lang = state.language
    visit = state.visit
    checking = state.step == "visit_blocked"
    if visit.sent is None:
        contact, failure = _read(token, state)
        if failure:
            return failure
        if visit.shown != _visit_fingerprint(contact) or not contact.can_add_visit:
            return _visit_turn(state, contact, "")
        visit.sent = _visit_values(contact, visit)
        visit.request_id = apartment_chat.new_request_id()
    result = crm_api.add_visit(token, state.contact_id, visit.sent, visit.request_id)
    body = result.body
    status = body.get("status")

    if status == "created":
        saved = body.get("visit")
        if saved:
            parts = [saved.get("visit_date") or "", saved.get("visit_type") or "", *(saved.get("done") or [])]
            line = say(lang, "visit_saved", summary=", ".join(p for p in parts if p))
        else:
            line = say(lang, "visit_saved_plain")
        answer = _reload(token, state, line)
        answer["outcome"] = "visit_created"
        return answer
    if status == "not_found":
        return _gone(state)
    if status == "approve_first":
        return _reload(token, state, say(lang, "approve_first"))
    if status == "uncertain":
        state.step = "visit_blocked"
        return _answer(state, "uncertain", say(lang, "visit_still_unknown" if checking else "visit_uncertain"))
    if status == "crm_unreachable":
        if checking:
            return _answer(state, "blocked", say(lang, "visit_still_unknown"))
        return _answer(state, "not_sent", say(lang, "visit_not_sent"), choices=yes_no_buttons(lang))
    if status == "invalid":
        if is_reused(body):
            return _reload(token, state, say(lang, "reused"))
        return _visit_refused(token, state, body)
    if status == "forbidden":
        return _reload(token, state, say(lang, "forbidden"))
    return _failure(state, body)


def _visit_refused(token: str, state: PersonState, body: dict) -> dict:
    """The CRM's 422 for a visit: one line per refused value, each naming its field by the
    CRM's current label; then the person again (the visit can be started over)."""
    lang = state.language
    contact, failure = _read(token, state)
    if failure:
        return failure
    labels = {f.field_key: field_label(f) for f in contact.new_visit_fields}
    lines = [invalid_reasons(lang, {"invalid_fields": [item]}, labels.get(item.get("field"), str(item.get("field"))))
             for item in body.get("invalid_fields") or [] if isinstance(item, dict)]
    reason = "\n".join(lines) or invalid_reasons(lang, {}, "")
    return _reload(token, state, say(lang, "visit_refused", reason=reason), contact=contact)


# --- CRM reads ------------------------------------------------------------------

def _read(token: str, state: PersonState):
    """(contact, None) or (None, the answer to return)."""
    if state.contact_id is None:
        return None, _answer(PersonState(language=state.language), "gone", say(state.language, "gone"), done=True)
    result = crm_api.get_contact(token, state.contact_id)
    status = result.body.get("status")
    if status == "ok":
        return Contact(**result.body["contact"]), None
    if status == "not_found":
        return None, _gone(state)
    return None, _failure(state, result.body)


def _gone(state: PersonState) -> dict:
    lang = state.language
    return _answer(PersonState(language=lang), "gone", say(lang, "gone"), done=True)


def _reload(token: Optional[str], state: PersonState, message: str, ask_value_for: Optional[str] = None,
            contact: Optional[Contact] = None) -> dict:
    """Re-read the person (unless `contact` is fresh), show them after `message`, and ask for the
    same field's value again (if still editable and `ask_value_for` is set) or show the menu."""
    lang = state.language
    if contact is None:
        contact, failure = _read(token, state)
        if failure:
            return failure
    intro = message + "\n" + contact_text(lang, contact)
    field = next((f for f in contact.fields if f.field_key == ask_value_for), None)
    if field is not None:
        return _ask_value(state, contact, field, intro=intro)
    return _menu(state, contact, intro=intro)


def _failure(state: PersonState, body: dict) -> dict:
    lang = state.language
    status = body.get("status")
    if status == "unauthorized":
        return _answer(PersonState(language=lang), "unauthorized", say(lang, "unauthorized"), logout=True)
    if status == "forbidden":
        return _answer(PersonState(language=lang), "forbidden", say(lang, "forbidden"), done=True)
    if status == "contacts_missing":
        return _answer(PersonState(language=lang), status, say(lang, status), done=True)
    if status in ("crm_not_configured", "crm_unreachable", "crm_timeout"):
        return _answer(state, status, say(lang, status))
    return _answer(state, "crm_failed", say(lang, "crm_failed", status=status or "unknown"))
