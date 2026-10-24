"""Placeholder CRM functions.

DEMO DATA ONLY. There is no CRM connection. Nothing here reads or changes
a real record.
"""

DEMO_LABEL = "DEMO DATA - not a real CRM record"


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


def create_contact_draft(name: str) -> dict:
    return {
        "label": DEMO_LABEL,
        "note": "Draft only. Nothing was saved to any CRM.",
        "draft": {"name": name, "status": "draft"},
    }


def pick_placeholder(message: str, action: str) -> tuple[str, dict]:
    """Choose which placeholder would run for an action ("find", "add" or "update")."""
    if action == "update":
        return "update_contact_field", update_contact_field("demo-001", "phone", "000-000-0001")
    if action == "add":
        return "create_contact_draft", create_contact_draft("New Contact Example")
    return "find_contact", find_contact(message)
