"""The add-contact conversation (docs/instructions15_the_whole_picture.md).

Stateless on the server. The chat page holds the conversation state (the
collected fields, the proposal currently shown, the language) in memory and
sends it with every turn; the user's CRM token comes separately, in the
Authorization header, and is passed only to the bridge (app/crm_api.py). The
state never contains the token, and nothing here is kept between requests, so
one login's fields and proposals can't reach another login.

Who does what:
- The page starts it (a search with no match, or "add <name>"); this module runs only for adding a person.
- The model (app/field_extraction.py) only turns a message into fields.
- The CRM, through the bridge, decides what is missing or invalid and makes
  the proposal. All saving goes through the bridge.
- Confirm is sent only when the message is an explicit yes (a fixed word
  list below - never the model's reading) while a proposal is shown, and only
  with that proposal's id, hash and exact fields. Any field change drops the
  proposal and asks for a new one, which needs a fresh yes.

Every reply is fixed wording (English or Hebrew, see MESSAGES), so the draft
shown is exactly the CRM's draft and each CRM code has one known meaning.
"""

import os
import string
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app import crm_api, field_extraction, generation

FIELD_NAMES = field_extraction.FIELD_NAMES
REQUIRED_FIELDS = ("first_name", "last_name")
FieldName = Literal["first_name", "last_name", "phone", "email", "address"]
Language = Literal["en", "he"]


class ContactDraftFields(BaseModel):
    """Same fields as the CRM's ContactDraftFields. Extra keys (organization_id, user_id ...) are refused."""

    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


class PendingProposal(BaseModel):
    """The proposal currently shown to the user, exactly as the CRM made it."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    payload_hash: str
    expires_at: Optional[str] = None
    fields: ContactDraftFields
    # True after a confirm whose answer was lost: only the same proposal may be confirmed again.
    uncertain: bool = False


class ConversationState(BaseModel):
    """Held by the chat page between turns. Contains no token."""

    model_config = ConfigDict(extra="forbid")

    fields: ContactDraftFields = Field(default_factory=ContactDraftFields)
    asked: list[FieldName] = Field(default_factory=list)
    pending: Optional[PendingProposal] = None
    # After an expired / unknown proposal: a "yes" makes a NEW proposal (never a confirm).
    restart_offered: bool = False
    language: Language = "en"


# --- explicit answers --------------------------------------------------------
# A message counts as yes / no only if, lowercased and without punctuation, it
# is exactly one of these. "yes but change the phone" is not a yes.
YES_WORDS = {
    "yes", "y", "yes please", "yes save", "yes save it", "save", "save it", "confirm", "i confirm", "yes confirm",
    "כן", "כן בבקשה", "כן שמור", "כן תשמור", "כן לשמור", "שמור", "תשמור", "אישור", "מאשר", "מאשרת", "מאשרים",
}
NO_WORDS = {
    "no", "n", "no thanks", "cancel", "stop",
    "לא", "לא תודה", "ביטול", "בטל", "תבטל",
}
# Leave an optional field empty when the CRM refused its value.
SKIP_WORDS = {"skip", "none", "no phone", "no email", "no address", "דלג", "אין", "בלי"}

PUNCTUATION = str.maketrans("", "", string.punctuation + "״׳!?.,")


def _normalized(text: str) -> str:
    return " ".join(text.lower().translate(PUNCTUATION).split())


def is_explicit_yes(text: str) -> bool:
    return _normalized(text) in YES_WORDS


def is_explicit_no(text: str) -> bool:
    return _normalized(text) in NO_WORDS


def is_skip(text: str) -> bool:
    return _normalized(text) in SKIP_WORDS


# --- wording -----------------------------------------------------------------
# Design choice (docs/contact-conversation-design.md): the CRM returns codes,
# these are the only texts shown for them.

FIELD_LABELS = {
    "en": {"first_name": "first name", "last_name": "last name", "phone": "phone",
           "email": "email", "address": "address"},
    "he": {"first_name": "שם פרטי", "last_name": "שם משפחה", "phone": "טלפון",
           "email": "אימייל", "address": "כתובת"},
}

MESSAGES = {
    "en": {
        "start": "Let's create a new contact. Tell me their details: first name and last name, "
                 "and if you have them, phone, email and address.",
        "ask": "Please tell me the {fields}.",
        "ask_one": "What is the {fields}? (I can't read free text right now, so one detail at a time.)",
        "not_understood": "I didn't find contact details in that. Please tell me the {fields}.",
        "draft": "Here is the exact draft that will be saved:\n{draft}\n\n"
                 "Save this contact as a draft? Reply \"yes\" to save exactly this, \"no\" to cancel, "
                 "or tell me what to change.",
        "ambiguous": "I need a clear answer before saving anything. Reply \"yes\" to save exactly the draft "
                     "above, \"no\" to cancel, or tell me what to change.",
        "cancelled": "Cancelled - nothing was saved. Tell me what to change if you want a new proposal, "
                     "or press Cancel to search for something else.",
        "cancelled_uncertain": "Stopped. I still don't know whether the earlier save went through - "
                               "please check the drafts in the CRM before adding this contact again.",
        "nothing_pending": "There is nothing waiting for confirmation, so nothing was saved.",
        "more_fields": "You can fill in more of this person's details now:",
        "created": "Saved as a draft contact (#{contact_id}):\n{draft}\n\n"
                   "It is waiting for approval in the CRM.",
        "replayed": "This contact was already saved by the earlier confirmation - it is the same contact "
                    "(#{contact_id}), no duplicate was created:\n{draft}\n\nIt is waiting for approval in the CRM.",
        "replayed_deleted": "This proposal was already saved earlier as contact #{contact_id}, which has since "
                            "been deleted in the CRM. Nothing new was saved.",
        "uncertain": "The CRM's answer was lost, so I don't know whether the draft was saved. "
                     "Reply \"yes\" to check safely with the same proposal (the CRM saves it at most once), "
                     "or \"no\" to stop.",
        "uncertain_waiting": "First I need to settle the earlier save, whose answer was lost. "
                             "Reply \"yes\" to check it safely, or \"no\" to stop.",
        "not_sent": "I couldn't reach the CRM, so the confirmation was not sent and nothing was saved. "
                    "Reply \"yes\" to try again.",
        "conflict": "The CRM couldn't confirm this right now; nothing new was saved. "
                    "Reply \"yes\" to try again with the same draft.",
        "payload_mismatch": "The CRM says the details don't match the proposal, so nothing was saved. "
                            "I asked for a fresh proposal.",
        "expired": "The proposal expired before it was confirmed, so nothing was saved. "
                   "Reply \"yes\" to make a new proposal with the same details, or tell me what to change.",
        "unknown_proposal": "The CRM has no such proposal for this login, so nothing was saved. "
                            "Reply \"yes\" to make a new proposal with the same details.",
        "unauthorized": "Your CRM login is no longer valid. Please log in again. Nothing was saved, "
                        "and the details you entered were cleared.",
        "forbidden": "The CRM does not allow your user to create contacts. Nothing was saved.",
        "crm_not_configured": "The assistant is not connected to a CRM (CRM_API_URL is not set). Nothing was sent.",
        "crm_unreachable": "I couldn't reach the CRM. Nothing was saved; send your message again to retry.",
        "crm_timeout": "The CRM didn't answer in time. Nothing was saved; send your message again to retry.",
        "crm_failed": "The CRM gave an answer I can't use ({status}). Nothing was saved by this step.",
        "confirm_failed": "The CRM gave an answer I can't use ({status}); the draft was not confirmed. "
                          "Reply \"yes\" to try again.",
        "invalid_phone": "The phone number \"{value}\" isn't valid.",
        "phone_taken": "Another contact already has the phone number \"{value}\".",
        "invalid_email": "The email \"{value}\" isn't a valid address.",
        "too_long": "The {field} is too long (at most {max_length} characters).",
        "invalid_other": "The CRM did not accept the {field} ({code}).",
        "skip_hint": " (or reply \"skip\" to leave it empty)",
        "empty": "-",
    },
    "he": {
        "start": "בואו ניצור איש קשר חדש. כתבו את הפרטים: שם פרטי ושם משפחה, "
                 "ואם יש - טלפון, אימייל וכתובת.",
        "ask": "אנא כתבו: {fields}.",
        "ask_one": "אנא כתבו: {fields}. (כרגע אינני יכול לקרוא טקסט חופשי, לכן פרט אחד בכל פעם.)",
        "not_understood": "לא מצאתי פרטי איש קשר בהודעה. אנא כתבו: {fields}.",
        "draft": "זו הטיוטה המדויקת שתישמר:\n{draft}\n\n"
                 "לשמור את איש הקשר כטיוטה? השיבו \"כן\" כדי לשמור בדיוק את זה, \"לא\" כדי לבטל, "
                 "או כתבו מה לשנות.",
        "ambiguous": "אני צריך תשובה ברורה לפני שמירה. השיבו \"כן\" כדי לשמור בדיוק את הטיוטה שלמעלה, "
                     "\"לא\" כדי לבטל, או כתבו מה לשנות.",
        "cancelled": "בוטל - לא נשמר דבר. כתבו מה לשנות אם תרצו הצעה חדשה, או לחצו על ביטול כדי לחפש משהו אחר.",
        "cancelled_uncertain": "הופסק. עדיין לא ידוע אם השמירה הקודמת הצליחה - "
                               "אנא בדקו את הטיוטות ב-CRM לפני שתוסיפו את איש הקשר שוב.",
        "nothing_pending": "אין כרגע שום דבר שממתין לאישור, ולכן לא נשמר דבר.",
        "more_fields": "אפשר למלא עכשיו עוד פרטים של האדם הזה:",
        "created": "נשמר כטיוטת איש קשר (#{contact_id}):\n{draft}\n\nהטיוטה ממתינה לאישור ב-CRM.",
        "replayed": "איש הקשר הזה כבר נשמר באישור הקודם - זה אותו איש קשר (#{contact_id}), "
                    "לא נוצר כפול:\n{draft}\n\nהטיוטה ממתינה לאישור ב-CRM.",
        "replayed_deleted": "ההצעה הזו כבר נשמרה בעבר כאיש קשר #{contact_id}, שנמחק מאז ב-CRM. "
                            "לא נשמר דבר חדש.",
        "uncertain": "התשובה מה-CRM אבדה, ולכן לא ידוע אם הטיוטה נשמרה. "
                     "השיבו \"כן\" כדי לבדוק בבטחה עם אותה הצעה (ה-CRM שומר אותה פעם אחת לכל היותר), "
                     "או \"לא\" כדי להפסיק.",
        "uncertain_waiting": "קודם צריך לברר את השמירה הקודמת, שהתשובה עליה אבדה. "
                             "השיבו \"כן\" כדי לבדוק בבטחה, או \"לא\" כדי להפסיק.",
        "not_sent": "לא הצלחתי להגיע ל-CRM, ולכן האישור לא נשלח ולא נשמר דבר. השיבו \"כן\" כדי לנסות שוב.",
        "conflict": "ה-CRM לא הצליח לאשר כרגע; לא נשמר דבר חדש. השיבו \"כן\" כדי לנסות שוב עם אותה טיוטה.",
        "payload_mismatch": "ה-CRM מדווח שהפרטים אינם תואמים להצעה, ולכן לא נשמר דבר. ביקשתי הצעה חדשה.",
        "expired": "תוקף ההצעה פג לפני שאושרה, ולכן לא נשמר דבר. "
                   "השיבו \"כן\" כדי ליצור הצעה חדשה עם אותם פרטים, או כתבו מה לשנות.",
        "unknown_proposal": "ב-CRM אין הצעה כזו עבור המשתמש הזה, ולכן לא נשמר דבר. "
                            "השיבו \"כן\" כדי ליצור הצעה חדשה עם אותם פרטים.",
        "unauthorized": "ההתחברות ל-CRM כבר אינה בתוקף. אנא התחברו שוב. לא נשמר דבר, והפרטים שהזנתם נמחקו.",
        "forbidden": "ה-CRM אינו מאפשר למשתמש שלכם ליצור אנשי קשר. לא נשמר דבר.",
        "crm_not_configured": "העוזר אינו מחובר ל-CRM (CRM_API_URL לא הוגדר). לא נשלח דבר.",
        "crm_unreachable": "לא הצלחתי להגיע ל-CRM. לא נשמר דבר; שלחו את ההודעה שוב כדי לנסות שוב.",
        "crm_timeout": "ה-CRM לא ענה בזמן. לא נשמר דבר; שלחו את ההודעה שוב כדי לנסות שוב.",
        "crm_failed": "ה-CRM החזיר תשובה שאינני יכול להשתמש בה ({status}). שלב זה לא שמר דבר.",
        "confirm_failed": "ה-CRM החזיר תשובה שאינני יכול להשתמש בה ({status}); הטיוטה לא אושרה. "
                          "השיבו \"כן\" כדי לנסות שוב.",
        "invalid_phone": "מספר הטלפון \"{value}\" אינו תקין.",
        "phone_taken": "למספר הטלפון \"{value}\" כבר יש איש קשר אחר.",
        "invalid_email": "כתובת האימייל \"{value}\" אינה תקינה.",
        "too_long": "השדה {field} ארוך מדי (עד {max_length} תווים).",
        "invalid_other": "ה-CRM לא קיבל את השדה {field} ({code}).",
        "skip_hint": " (או השיבו \"דלג\" כדי להשאיר ריק)",
        "empty": "—",
    },
}


def say(language: str, key: str, **values) -> str:
    return MESSAGES[language][key].format(**values)


def field_list(language: str, names) -> str:
    labels = [FIELD_LABELS[language][n] for n in names]
    if language == "he":
        return ", ".join(labels)
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def draft_lines(language: str, fields: dict) -> str:
    empty = MESSAGES[language]["empty"]
    return "\n".join(
        f"- {FIELD_LABELS[language][n].capitalize() if language == 'en' else FIELD_LABELS[language][n]}: "
        f"{fields.get(n) or empty}"
        for n in FIELD_NAMES
    )


# --- one turn ------------------------------------------------------------------

def _answer(state: ConversationState, outcome: str, reply: str, *, logout: bool = False,
            done: bool = False) -> dict:
    """What the page gets back: the reply, the new state to hold, and what happened."""
    pending = state.pending
    return {
        "intent": "create_contact",
        "outcome": outcome,
        "reply": reply,
        "state": state.model_dump(),
        # The draft the user is being asked to confirm (None when nothing waits for a yes).
        "awaiting_confirmation": pending.fields.model_dump() if pending and not pending.uncertain else None,
        "logout": logout,
        "show_menu": done,
    }


def start(language: str) -> dict:
    """Adding a person: a fresh conversation, nothing sent anywhere."""
    state = ConversationState(language=language, asked=list(REQUIRED_FIELDS))
    return _answer(state, "started", say(language, "start"))


def handle_turn(token: str, message: str, state: ConversationState) -> dict:
    """The language is the user's CRM language (state.language): this module never guesses it."""
    text = message.strip()
    lang = state.language
    pending = state.pending

    if pending is not None:
        if pending.fields != state.fields:
            # A proposal for other details than the ones held now is stale: never confirm it.
            state.pending = None
            return _propose(token, state)
        if is_explicit_yes(text):
            return _confirm(token, state)
        if is_explicit_no(text):
            state.pending = None
            key = "cancelled_uncertain" if pending.uncertain else "cancelled"
            return _answer(state, "cancelled", say(lang, key))
        if pending.uncertain:
            # Never a new proposal while the last save is unknown.
            return _answer(state, "uncertain", say(lang, "uncertain_waiting"))
        if not _apply_message(text, state):
            return _answer(state, "awaiting_confirmation", say(lang, "ambiguous"))
        state.pending = None  # the details changed: the old proposal is dropped, a fresh yes is needed
        return _propose(token, state)

    if state.restart_offered and is_explicit_yes(text):
        state.restart_offered = False
        return _propose(token, state)  # a new proposal only; confirming needs another yes
    state.restart_offered = False

    if is_explicit_yes(text):
        return _answer(state, "nothing_pending", say(lang, "nothing_pending") + " " + _ask_text(state))
    if is_explicit_no(text):
        return _answer(ConversationState(language=lang), "cancelled", say(lang, "cancelled"), done=True)

    if state.asked and is_skip(text) and all(n not in REQUIRED_FIELDS for n in state.asked):
        for name in state.asked:
            setattr(state.fields, name, None)
        return _propose(token, state)

    if not _apply_message(text, state):
        return _answer(state, "needs_information", _ask_text(state, not_understood=True))
    return _propose(token, state)


def _apply_message(text: str, state: ConversationState) -> bool:
    """Read fields from the message into state.fields; True if any field changed."""
    extraction = field_extraction.extract_fields(text, list(state.asked))
    if extraction.status == "ok":
        found = extraction.fields
    elif len(state.asked) == 1 and text:
        found = {state.asked[0]: text}  # no model: the answer to the one question asked
    else:
        state.asked = state.asked[:1] or _missing_required(state)[:1] or ["first_name"]
        found = {}
    changed = False
    for name, value in found.items():
        if getattr(state.fields, name) != value:
            setattr(state.fields, name, value)
            changed = True
    return changed


def _missing_required(state: ConversationState) -> list[str]:
    return [n for n in REQUIRED_FIELDS if not getattr(state.fields, n)]


def _ask_text(state: ConversationState, not_understood: bool = False) -> str:
    asked = state.asked or _missing_required(state) or list(REQUIRED_FIELDS)
    fields = field_list(state.language, asked)
    if not_understood and len(asked) == 1 and _extraction_off():
        return say(state.language, "ask_one", fields=fields)
    return say(state.language, "not_understood" if not_understood else "ask", fields=fields)


def _extraction_off() -> bool:
    provider = generation.provider_name()
    variable = generation.KEY_VARIABLES.get(provider)
    return not variable or not os.environ.get(variable, "").strip()


def _invalid_text(lang: str, invalid: list[dict]) -> str:
    lines = []
    for item in invalid:
        name = item.get("field")
        code = item.get("code")
        label = FIELD_LABELS[lang].get(name, str(name))
        if code in ("invalid_phone", "phone_taken", "invalid_email"):
            line = say(lang, code, value=item.get("value", ""))
        elif code == "too_long":
            line = say(lang, "too_long", field=label, max_length=item.get("max_length", "?"))
        else:
            line = say(lang, "invalid_other", field=label, code=code)
        if name not in REQUIRED_FIELDS:
            line += MESSAGES[lang]["skip_hint"]
        lines.append(line)
    return "\n".join(lines)


def _propose(token: str, state: ConversationState) -> dict:
    lang = state.language
    result = crm_api.request_proposal(token, state.fields.model_dump())
    body = result.body
    status = body.get("status")

    if status in ("needs_information", "invalid"):
        returned = body.get("fields") or state.fields.model_dump()
        invalid = [i for i in body.get("invalid_fields") or [] if isinstance(i, dict)]
        for item in invalid:
            # Show the refused value, then drop it so it is never resubmitted unchanged.
            item["value"] = returned.get(item.get("field")) or getattr(state.fields, item.get("field", ""), None)
            returned[item.get("field")] = None
        state.fields = ContactDraftFields(**{n: _text_or_none(returned.get(n)) for n in FIELD_NAMES})
        invalid_names = [i["field"] for i in invalid if i.get("field") in FIELD_NAMES]
        missing = [n for n in body.get("missing_fields") or [] if n in FIELD_NAMES]
        state.asked = list(dict.fromkeys(invalid_names + missing)) or _missing_required(state)
        parts = [_invalid_text(lang, invalid)] if invalid else []
        parts.append(say(lang, "ask", fields=field_list(lang, state.asked)))
        return _answer(state, "needs_information", "\n".join(parts))

    if status == "proposal":
        draft = body.get("draft") or {}
        state.fields = ContactDraftFields(**{n: _text_or_none(draft.get(n)) for n in FIELD_NAMES})
        state.pending = PendingProposal(
            proposal_id=str(body.get("proposal_id")),
            payload_hash=str(body.get("payload_hash")),
            expires_at=body.get("expires_at"),
            fields=state.fields.model_copy(),
        )
        state.asked = []
        return _answer(state, "proposal", say(lang, "draft", draft=draft_lines(lang, state.fields.model_dump())))

    return _failure(state, status)


def _confirm(token: str, state: ConversationState) -> dict:
    lang = state.language
    pending = state.pending
    result = crm_api.confirm_proposal(token, pending.proposal_id, pending.payload_hash,
                                      pending.fields.model_dump())
    body = result.body
    status = body.get("status")

    if status == "created":
        contact = body.get("contact")
        fresh = ConversationState(language=lang)
        if contact is None:
            return _answer(fresh, "created", say(lang, "replayed_deleted", contact_id=body.get("contact_id")),
                           done=True)
        key = "replayed" if body.get("replayed") else "created"
        saved = {n: contact.get(n) for n in FIELD_NAMES}
        answer = _answer(fresh, key, say(lang, key, contact_id=body.get("contact_id"), draft=draft_lines(lang, saved)),
                         done=True)
        answer["contact_id"] = body.get("contact_id")  # the caller may open the new contact for more fields
        return answer
    if status == "uncertain":
        pending.uncertain = True
        return _answer(state, "uncertain", say(lang, "uncertain"))
    if status == "crm_unreachable":  # no connection: the confirm was never sent
        return _answer(state, "not_sent", say(lang, "not_sent"))
    if status == "conflict":
        return _answer(state, "conflict", say(lang, "conflict"))
    if status == "payload_mismatch":
        state.pending = None
        answer = _propose(token, state)
        answer["reply"] = say(lang, "payload_mismatch") + "\n\n" + answer["reply"]
        return answer
    if status in ("expired", "unknown_proposal"):
        state.pending = None
        state.restart_offered = True
        return _answer(state, status, say(lang, status))
    if status in ("unauthorized", "forbidden", "crm_not_configured"):
        return _failure(state, status)
    # crm_redirect_refused, crm_contract_error, crm_error ...: not confirmed; the same proposal may be retried.
    return _answer(state, "confirm_failed", say(lang, "confirm_failed", status=status))


def _failure(state: ConversationState, status: str | None) -> dict:
    lang = state.language
    if status == "unauthorized":
        return _answer(ConversationState(language=lang), "unauthorized", say(lang, "unauthorized"), logout=True)
    if status == "forbidden":
        state.pending = None
        return _answer(state, "forbidden", say(lang, "forbidden"))
    if status in ("crm_not_configured", "crm_unreachable", "crm_timeout"):
        return _answer(state, status, say(lang, status))
    return _answer(state, "crm_failed", say(lang, "crm_failed", status=status or "unknown"))


def _text_or_none(value) -> str | None:
    return value if isinstance(value, str) and value else None
