"""Placeholder CRM functions for the keyword-routed /chat actions.

DEMO DATA ONLY. There is no CRM connection. Nothing here reads or changes
a real record. Adding a contact is no longer a demo: it is the real
"Create a contact" conversation (app/contact_chat.py), started from the menu.
"""

DEMO_LABEL = {"en": "DEMO DATA - not a real CRM record", "he": "נתוני הדגמה - לא רשומה אמיתית ב-CRM"}
DEMO_NOTE = {
    "en": "No record was changed. This only shows what an update would look like.",
    "he": "שום רשומה לא שונתה. זו רק הדגמה של איך עדכון היה נראה.",
}
ADD_CONTACT_POINTER = {
    "en": "To create a contact, choose \"Create a contact\" from the menu (after logging in). "
          "Nothing was saved by this message.",
    "he": "כדי ליצור איש קשר, בחרו \"יצירת איש קשר\" בתפריט (אחרי ההתחברות). ההודעה הזו לא שמרה דבר.",
}


def find_contact(query: str, language: str = "en") -> dict:
    return {
        "label": DEMO_LABEL[language],
        "query": query,
        "contact": {"id": "demo-001", "name": "Dana Example", "phone": "000-000-0000"},
    }


def update_contact_field(contact_id: str, field: str, value: str, language: str = "en") -> dict:
    return {
        "label": DEMO_LABEL[language],
        "note": DEMO_NOTE[language],
        "would_update": {"contact_id": contact_id, "field": field, "value": value},
    }


def pick_placeholder(message: str, action: str, language: str = "en") -> tuple[str, dict]:
    """Choose which placeholder would run for an action ("find" or "update")."""
    if action == "update":
        return "update_contact_field", update_contact_field("demo-001", "phone", "000-000-0001", language)
    return "find_contact", find_contact(message, language)
