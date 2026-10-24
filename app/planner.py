"""Temporary keyword router (English and Hebrew).

This is a stub. Later this function will be replaced by a model call that
decides the route. For now it uses one simple rule:

- action: the message is a command that STARTS with a find / add / update verb
  (optionally after "please" / "בבקשה") and does not end with "?".
  Words are matched whole, so "address" is not "add". A verb inside a question
  ("how do I add a contact?") is not an action.
- clarify: the message is empty or only spaces.
- knowledge: everything else (searched in the user guides).
"""

import re

ROUTE_SOURCE = "keyword-stub"

# Leading command verbs, by demo action. Hebrew lists masculine, feminine and
# plural imperatives plus the common future-tense request form ("תוסיף").
ACTION_VERBS = {
    "update": (
        "update",
        "עדכן", "עדכני", "עדכנו", "תעדכן", "תעדכני", "תעדכנו",
    ),
    "add": (
        "add", "create",
        "הוסף", "הוסיפי", "הוסיפו", "תוסיף", "תוסיפי", "תוסיפו",
        "צור", "צרי", "צרו", "תיצור", "תיצרי", "תיצרו",
    ),
    "find": (
        "find", "search",
        "מצא", "מצאי", "מצאו", "תמצא", "תמצאי", "תמצאו",
        "חפש", "חפשי", "חפשו", "תחפש", "תחפשי", "תחפשו",
    ),
}
POLITE_WORDS = ("please", "בבקשה")

WORD = re.compile(r"\w+")


def action_verb(message: str) -> str | None:
    """Return "find", "add" or "update" if the message is a command, else None."""
    text = message.strip().lower()
    if text.endswith("?"):
        return None
    words = WORD.findall(text)
    while words and words[0] in POLITE_WORDS:
        words = words[1:]
    if not words:
        return None
    for action, verbs in ACTION_VERBS.items():
        if words[0] in verbs:
            return action
    return None


def plan(message: str) -> dict:
    """Return the route ("action", "knowledge" or "clarify"), the action kind and where it came from."""
    action = None
    if not message.strip():
        route = "clarify"
    else:
        action = action_verb(message)
        route = "action" if action else "knowledge"
    return {"route": route, "action": action, "route_source": ROUTE_SOURCE}
