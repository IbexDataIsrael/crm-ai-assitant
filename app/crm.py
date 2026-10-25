"""Placeholder CRM functions for the keyword-routed /chat actions.

DEMO DATA ONLY. There is no CRM connection. Nothing here reads or changes
a real record. Adding a contact is no longer a demo: it is the real
"Create a contact" conversation (app/contact_chat.py), started from the menu.
"""

DEMO_LABEL = "DEMO DATA - not a real CRM record"
ADD_CONTACT_POINTER = (
    "To create a contact, choose \"Create a contact\" from the menu (after logging in). "
    "Nothing was saved by this message."
)


def find_contact(query: str) -> dict:
    return {
        "label": DEMO_LABEL,
        "query": query,
        "contact": {"id": "demo-001", "name": "Dana Example", "phone": "000-000-0000"},
    }


def update_contact_field(contact_id: str, field: str, value: str) -> dict:
    return {
        "label": DEMO_LABEL,
        "note": "No record was changed. This only shows what an update would look like.",
        "would_update": {"contact_id": contact_id, "field": field, "value": value},
    }


def pick_placeholder(message: str, action: str) -> tuple[str, dict]:
    """Choose which placeholder would run for an action ("find" or "update")."""
    if action == "update":
        return "update_contact_field", update_contact_field("demo-001", "phone", "000-000-0001")
    return "find_contact", find_contact(message)
