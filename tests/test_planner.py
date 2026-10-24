"""Keyword router rules (no model, no retrieval)."""

import pytest

from app.planner import plan


@pytest.mark.parametrize(
    "message, action",
    [
        ("find contact Dana", "find"),
        ("Find Dana Example", "find"),
        ("search for Dana", "find"),
        ("add a contact", "add"),
        ("Please add a contact named Dana.", "add"),
        ("create a new contact", "add"),
        ("update the phone number", "update"),
        ("הוסף איש קשר", "add"),
        ("בבקשה תוסיף איש קשר", "add"),
        ("מצא את דנה", "find"),
        ("חפש איש קשר", "find"),
        ("עדכן את מספר הטלפון", "update"),
    ],
)
def test_commands_starting_with_a_verb_are_actions(message, action):
    assert plan(message) == {"route": "action", "action": action, "route_source": "keyword-stub"}


@pytest.mark.parametrize(
    "message",
    [
        "what is jewishpoints",
        "מה זה נקודות יהודיות",
        "why can't I see a contact that the assistant added?",
        "how do I add a contact?",
        "Can you add a contact?",            # a verb inside a question is not a command
        "add a contact?",                    # ends with "?": treated as a question
        "address of the office",             # "add" inside "address" is not "add"
        "updates to the visit schedule",     # "updates" is not "update"
        "finding a contact's apartment",
        "איך מוסיפים איש קשר?",
        "hello",
    ],
)
def test_other_input_goes_to_knowledge(message):
    assert plan(message) == {"route": "knowledge", "action": None, "route_source": "keyword-stub"}


@pytest.mark.parametrize("message", ["", " ", "\n\t"])
def test_empty_input_is_clarify(message):
    assert plan(message) == {"route": "clarify", "action": None, "route_source": "keyword-stub"}
