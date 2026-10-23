"""Temporary keyword router.

This is a stub. Later this function will be replaced by a model call that
decides the route. For now it only looks for a few keywords.
"""

ROUTE_SOURCE = "keyword-stub"

ACTION_WORDS = ("contact", "find", "update", "add")
KNOWLEDGE_WORDS = ("how", "instructions")


def plan(message: str) -> dict:
    """Return the route ("action", "knowledge" or "clarify") and where it came from."""
    text = message.lower()
    if any(word in text for word in ACTION_WORDS):
        route = "action"
    elif any(word in text for word in KNOWLEDGE_WORDS):
        route = "knowledge"
    else:
        route = "clarify"
    return {"route": route, "route_source": ROUTE_SOURCE}
