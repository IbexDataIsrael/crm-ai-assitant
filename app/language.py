"""The user's language (docs/instructions19.md): one shared rule for every channel.

The language belongs to the CRM user: the CRM stores it as the preference
"locale" ("he" or "en", GET/PUT /api/preferences). That is the only stored
copy. The assistant reads it at login and otherwise keeps it in the session
only - the page holds a LanguageState and sends it with every turn (a future
WhatsApp client must keep the same state and apply the same rule).

Last-used language, conservative rule:
- Only typed free text counts (an Ask question, a free message in the contact
  flow). Menu choices, buttons, confirm words, leading command verbs, field
  values, URLs, emails, digits and punctuation never count.
- What is left is "clearly" Hebrew or English only with at least 4 letters
  (Hebrew U+0590-U+05FF, Latin A-Z a-z) of which one script is at least 80%.
  Anything else leaves the language unchanged.
- Without a toggle press in this session one clear message in the other
  language switches; after a toggle press two in a row are needed. A clear
  message in the current language breaks the row; an unclear one doesn't
  count either way. A toggle press applies at once and resets the row.
- A switch is saved once (PUT locale); never the same value twice. A failed
  save keeps the language for the session, never blocks the turn, and is
  tried once more on the next turn.

Privacy: only the two-letter value goes to the CRM. No message text and no
detection scores are stored or logged.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app import crm_api, planner
from app.contact_chat import is_explicit_no, is_explicit_yes, is_skip

Language = Literal["en", "he"]
LANGUAGES = ("en", "he")

MIN_LETTERS = 4
CLEAR_SHARE = 0.8

HEBREW_LETTER = re.compile(r"[֐-׿]")
LATIN_LETTER = re.compile(r"[A-Za-z]")
URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
EMAIL = re.compile(r"\S+@\S+")
WORD = re.compile(r"\w+")

NOTICES = {
    "saved": {"en": "Language saved.", "he": "השפה נשמרה."},
    "save_failed": {"en": "I could not save your language. It will stay for this session.",
                    "he": "לא הצלחתי לשמור את השפה. היא תישאר לשיחה הזו."},
}


class LanguageState(BaseModel):
    """Held by the client between turns, like the conversation states. No token, no message text."""

    model_config = ConfigDict(extra="forbid")

    language: Language = "en"
    # The user pressed the toggle in this session: two clear messages are then needed to switch.
    toggled: bool = False
    # Clear messages in the other language in a row (0 or 1 - the second one switches).
    streak: int = Field(default=0, ge=0, le=1)
    # The value the CRM is known to hold (from login or the last good save); None = unknown / unset.
    saved: Optional[Language] = None
    # A save that failed: tried once more on the next turn.
    retry: Optional[Language] = None


def _without_leading_command(text: str) -> str:
    """Drop a leading "please" and a request verb (find / add / update, מצא / הוסף / עדכן ...)."""
    verbs = {v for group in planner.ACTION_VERBS.values() for v in group}
    while True:
        match = WORD.match(text.lstrip())
        if not match:
            return text
        word = match.group(0).lower()
        if word not in verbs and word not in planner.POLITE_WORDS:
            return text
        text = text.lstrip()[match.end():]


def clear_language(text: str, ignore=()) -> Optional[Language]:
    """"he" or "en" if the counted text is clearly one language, else None."""
    if is_explicit_yes(text) or is_explicit_no(text) or is_skip(text):
        return None
    for value in ignore:
        if isinstance(value, str) and value.strip():
            text = re.sub(re.escape(value.strip()), " ", text, flags=re.IGNORECASE)
    text = EMAIL.sub(" ", URL.sub(" ", text))
    text = _without_leading_command(text)
    hebrew = len(HEBREW_LETTER.findall(text))
    latin = len(LATIN_LETTER.findall(text))
    letters = hebrew + latin
    if letters < MIN_LETTERS:
        return None
    if hebrew >= CLEAR_SHARE * letters:
        return "he"
    if latin >= CLEAR_SHARE * letters:
        return "en"
    return None


def observe(state: LanguageState, text: str, ignore=()) -> LanguageState:
    """Apply one typed free-text message to the session language."""
    seen = clear_language(text, ignore)
    if seen is None:
        return state
    if seen == state.language:
        state.streak = 0
        return state
    needed = 2 if state.toggled else 1
    if state.streak + 1 >= needed:
        state.language = seen
        state.streak = 0
    else:
        state.streak += 1
    return state


def save(token: str, language: str) -> crm_api.BridgeResult:
    """PUT the locale preference. The only write the assistant makes for the language."""
    return crm_api.save_language(token, language)


def settle(token: str, state: LanguageState, changed: bool) -> Optional[str]:
    """After a turn: save a change (or retry a failed one once). Returns the notice key, if any.

    Never blocks the turn: whatever the CRM answers, the session keeps its language.
    """
    if state.saved == state.language:
        state.retry = None
        return None
    if changed:
        result = save(token, state.language)
        if result.http_status == 200:
            state.saved, state.retry = state.language, None
            return "saved"
        state.retry = state.language
        return "save_failed"
    if state.retry == state.language:
        state.retry = None  # the one retry
        result = save(token, state.language)
        if result.http_status == 200:
            state.saved = state.language
            return "saved"
    return None


def notice(key: Optional[str], language: str) -> Optional[str]:
    return NOTICES[key][language] if key else None
