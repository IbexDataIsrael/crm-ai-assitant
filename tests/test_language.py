"""The user's language (docs/instructions19.md, instructions20.md): the CRM's interface
language, read at login and on a resumed login, never changed by the chat, and every reply in it.

CRM and model are always mocks (fake_crm / fake_gemini in conftest.py).
"""

import importlib
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app import field_extraction, generation, knowledge
from app.main import app
from tests.conftest import TEST_AUTH, FakeInteraction

PASSWORD = "Correct-Horse-9-secret"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.login-token-secret.sig"
LOGIN_OK = {"access_token": TOKEN, "token_type": "bearer", "requires_org_selection": False, "organizations": []}

# One passage from each guide: the search is never filtered by language.
BOTH_GUIDES = [
    {"score": 0.6, "filename": "about_hebrew.md", "heading": "1. מהי המערכת הזו?", "text": "מערכת CRM דו-לשונית."},
    {"score": 0.5, "filename": "about_english.md", "heading": "1. What this system is", "text": "A bilingual CRM."},
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def searched(monkeypatch):
    """knowledge.search stand-in: records each query, returns a passage from each guide."""
    queries = []

    def search(message, k=knowledge.TOP_K):
        queries.append(message)
        return [dict(p) for p in BOTH_GUIDES]

    monkeypatch.setattr(knowledge, "search", search)
    return queries


def ask(client, message, language=None, intent="ask_question"):
    body = {"intent": intent, "message": message}
    if language is not None:
        body["language"] = language
    response = client.post("/assistant/turn", json=body, headers=TEST_AUTH)
    assert response.status_code == 200, response.text
    return response.json()


# --- login reads the CRM's locale --------------------------------------------------

@pytest.mark.parametrize("preferences, expected", [
    (httpx.Response(200, json={"locale": "he", "contacts.columns": ["phone"]}), "he"),
    (httpx.Response(200, json={"locale": "en", "theme": "dark"}), "en"),
    (httpx.Response(200, json={"contacts.columns": ["phone", "owner"]}), None),  # never set
    (httpx.Response(200, json={"locale": "fr"}), None),
    (httpx.Response(200, json={"locale": ["he"]}), None),
    (httpx.Response(500, json={"detail": "boom"}), None),
    (httpx.Response(200, text="not json"), None),
    (httpx.ConnectError("refused"), None),
])
def test_login_answer_carries_only_the_crm_language(client, fake_crm, preferences, expected):
    fake_crm.answers.append(httpx.Response(200, json=LOGIN_OK))
    fake_crm.pref_answers.append(preferences)
    response = client.post("/auth/login", json={"username": "dana@example.org", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json() == {"status": "logged_in", "access_token": TOKEN, "language": expected}
    # Read once, with the new token; other preference keys never reach the page.
    assert [r.method for r in fake_crm.pref_requests] == ["GET"]
    assert fake_crm.pref_requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    for other in ("contacts.columns", "theme", "dark", "owner"):
        assert other not in response.text


def test_a_refused_login_reads_no_preferences(client, fake_crm):
    fake_crm.answers.append(httpx.Response(401, json={"detail": "Incorrect username or password"}))
    response = client.post("/auth/login", json={"username": "dana@example.org", "password": PASSWORD})
    assert response.status_code == 401 and "language" not in response.json()
    assert fake_crm.pref_requests == []


# --- the chat never changes the language (instructions20) ---------------------------

@pytest.mark.parametrize("body", [{"language": "he"}, {"language": "en"}])
def test_there_is_no_language_save_route(client, fake_crm, body):
    response = client.post("/assistant/language", json=body, headers=TEST_AUTH)
    assert response.status_code in (404, 405)
    assert fake_crm.pref_requests == [] and fake_crm.me_requests == []


def test_a_language_state_from_an_old_page_is_refused(client, fake_crm):
    response = client.post("/assistant/turn", headers=TEST_AUTH, json={
        "intent": "ask_question", "message": "", "language": "he", "language_state": {"language": "he"}})
    assert response.status_code == 422


def test_a_hebrew_question_in_an_english_session_is_answered_in_english_and_nothing_is_saved(
        client, fake_crm, searched, fake_gemini):
    fake_gemini.reply = FakeInteraction("A bilingual CRM [2].")
    data = ask(client, "מה זה נקודות יהודיות?", "en")
    assert data["language"] == "en" and data["reply"].startswith("AI-generated answer")
    assert "Write the answer in English." in fake_gemini.requests[0]["system_instruction"]
    assert "language_state" not in data and "language_notice" not in data
    assert fake_crm.pref_requests == []


@pytest.mark.parametrize("intent", ["create_contact", "edit_apartment", "ask_question", "update_contact"])
def test_a_menu_choice_answers_in_the_sent_language(client, fake_crm, intent):
    data = ask(client, "", "he", intent=intent)
    assert re.search(r"[֐-׿]", data["reply"])
    assert fake_crm.pref_requests == []


def test_a_hebrew_street_name_in_an_english_apartment_session_changes_nothing(client, fake_crm):
    fake_crm.answers.append(httpx.Response(200, json={"results": [], "truncated": False}))
    data = ask(client, "הרצל 10 תל אביב יפו", "en", intent="edit_apartment")
    assert data["reply"].startswith("I found no apartment") and fake_crm.pref_requests == []


def test_a_free_hebrew_sentence_in_an_english_contact_flow_stays_english(client, fake_crm, monkeypatch):
    monkeypatch.setattr(field_extraction, "extract_fields",
                        lambda message, asked: field_extraction.Extraction("ok", fields={}))
    data = ask(client, "אני רוצה להוסיף מישהו חדש למערכת", "en", intent="create_contact")
    assert data["state"]["language"] == "en" and not re.search(r"[֐-׿]", data["reply"])
    assert fake_crm.pref_requests == []


def test_without_a_sent_language_the_held_state_decides_then_english(client, fake_crm):
    held = ask(client, "", "he", intent="create_contact")["state"]
    response = client.post("/assistant/turn", headers=TEST_AUTH,
                           json={"intent": "create_contact", "message": "no", "state": held})
    assert re.search(r"[֐-׿]", response.json()["reply"])
    assert not re.search(r"[֐-׿]", ask(client, "", intent="create_contact")["reply"])


# --- Ask: both guides searched, the answer in the user's language -------------------

@pytest.mark.parametrize("message, language", [("What is this system?", "en"), ("מה זה המערכת?", "he"),
                                               ("What is this system?", "he")])
def test_ask_searches_both_guides_and_names_the_users_language(
        client, fake_crm, searched, fake_gemini, message, language):
    # The last case: an English question from a Hebrew CRM user is still answered in Hebrew.
    fake_gemini.reply = FakeInteraction("[1]")
    data = ask(client, message, language)
    assert searched == [message]  # one search, no language filter
    request = fake_gemini.requests[0]
    name = {"en": "English", "he": "Hebrew"}[language]
    assert f"Write the answer in {name}." in request["system_instruction"]
    assert "about_hebrew.md" in request["input"] and "about_english.md" in request["input"]
    assert data["language"] == language


def test_chat_route_takes_the_language_too(client, fake_crm, searched, fake_gemini):
    fake_gemini.reply = FakeInteraction("מערכת CRM [1].")
    response = client.post("/chat", json={"message": "What is this system?", "language": "he"}, headers=TEST_AUTH)
    assert response.status_code == 200
    assert "Write the answer in Hebrew." in fake_gemini.requests[0]["system_instruction"]
    assert response.json()["reply"].startswith("תשובה שנכתבה ב-AI")


# --- every string the server writes has both languages -----------------------------

TEXT_MODULES = ("app.main", "app.contact_chat", "app.apartment_chat", "app.knowledge", "app.crm",
                "app.generation", "app.search_chat")


def _bilingual_tables():
    """Every module-level dict (at any depth) keyed by language. Found automatically, so a new
    table is checked without being listed here."""
    found = []

    def walk(name, value):
        if isinstance(value, dict):
            if "en" in value or "he" in value:
                found.append((name, value))
            for key, inner in value.items():
                walk(f"{name}[{key!r}]", inner)

    for module_name in TEXT_MODULES:
        module = importlib.import_module(module_name)
        for attribute, value in vars(module).items():
            if attribute.isupper():
                walk(f"{module_name}.{attribute}", value)
    return found


def _same_shape(en, he, where):
    if isinstance(en, dict):
        assert isinstance(he, dict) and set(en) == set(he), f"{where}: keys differ"
        for key in en:
            _same_shape(en[key], he[key], f"{where}[{key!r}]")
    else:
        assert isinstance(en, str) and isinstance(he, str) and en.strip() and he.strip(), where
        assert set(re.findall(r"\{(\w+)", en)) == set(re.findall(r"\{(\w+)", he)), f"{where}: placeholders differ"
        if re.search(r"[A-Za-z]{2,}", re.sub(r"\{[^}]*\}", "", en)):  # words, not just a mark like "-"
            assert re.search(r"[֐-׿]", he), f"{where}: the Hebrew text has no Hebrew"


# Keyed by language but never shown to a user: the target language's name inside the model's
# instruction ("Write the answer in Hebrew").
NOT_SHOWN_TO_USERS = {"app.generation.LANGUAGE_NAMES"}


def test_every_text_table_has_both_languages_with_the_same_keys():
    tables = _bilingual_tables()
    names = {name for name, _ in tables}
    # The ones known today must all be found (so the scan itself is working).
    for expected in ("app.main.GATE_REPLIES['unauthorized']", "app.main.UPDATE_NOT_AVAILABLE", "app.main.CLARIFY",
                     "app.main.PLACEHOLDER_REPLY", "app.contact_chat.MESSAGES", "app.apartment_chat.MESSAGES",
                     "app.knowledge.TEXTS", "app.crm.ADD_CONTACT_POINTER"):
        assert expected in names, expected
    for name, table in tables:
        if name in NOT_SHOWN_TO_USERS:
            continue
        if set(table) <= {"en", "he"}:
            assert set(table) == {"en", "he"}, f"{name}: only {sorted(table)}"
            _same_shape(table["en"], table["he"], name)


ENGLISH_SENTENCE = re.compile(r"[A-Za-z]{2,}(?:[ ,'-]+[A-Za-z]{2,}){2,}")


def _english_left(reply, allowed=()):
    for text in allowed:
        reply = reply.replace(text, " ")
    return ENGLISH_SENTENCE.findall(reply)


@pytest.mark.parametrize("status", ["answered", "insufficient_sources", "no_api_key", "error"])
def test_hebrew_ask_replies_have_no_english_text_of_the_assistants_own(client, fake_crm, searched, monkeypatch, status):
    result = generation.GenerationResult(
        status, "gemini-x", provider="Gemini",
        answer="מערכת CRM דו-לשונית [1]." if status == "answered" else None,
        citations=[{"number": 1, "filename": "about_hebrew.md", "heading": "1. מהי המערכת הזו?"}]
        if status == "answered" else [],
        error={"no_api_key": "GEMINI_API_KEY is not set", "error": "Could not reach Gemini (ConnectError)"}.get(status))
    monkeypatch.setattr(generation, "generate_answer", lambda *a, **k: result)
    data = ask(client, "מה זה?", "he")
    # Allowed as they are: guide passages, file names and headings, and the provider's technical detail.
    allowed = [p["text"] for p in BOTH_GUIDES] + [p["heading"] for p in BOTH_GUIDES] + [result.error or ""]
    assert _english_left(data["reply"], allowed) == []


@pytest.mark.parametrize("message", ["", "מצא את דנה", "עדכן את הטלפון של דנה", "הוסף איש קשר"])
def test_hebrew_chat_route_replies_have_no_english_sentences(client, fake_crm, message):
    data = client.post("/chat", json={"message": message, "language": "he"}, headers=TEST_AUTH).json()
    assert _english_left(data["reply"], ["Dana Example"]) == [], data["reply"]


@pytest.mark.parametrize("me_answer, status", [(None, 401), (httpx.Response(403, json={}), 403),
                                               (httpx.Response(500, json={}), 503)])
def test_gate_replies_follow_the_session_language(client, fake_crm, me_answer, status):
    headers = {} if me_answer is None else TEST_AUTH
    if me_answer is not None:
        fake_crm.me_answers.append(me_answer)
    response = client.post("/assistant/turn", headers=headers, json={
        "intent": "ask_question", "message": "x", "language": "he"})
    assert response.status_code == status
    assert re.search(r"[֐-׿]", response.json()["reply"]) and not _english_left(response.json()["reply"])


def test_hebrew_contact_and_apartment_starts_have_no_english(client, fake_crm):
    for intent in ("create_contact", "edit_apartment", "update_contact"):
        data = ask(client, "", "he", intent=intent)
        assert _english_left(data["reply"]) == [], (intent, data["reply"])


def test_typed_text_never_reaches_the_preferences_or_the_log(client, fake_crm, searched, fake_gemini, caplog):
    fake_gemini.reply = FakeInteraction("מערכת CRM [1].")
    message = "מה זה נקודות יהודיות ומי משתמש בה?"
    ask(client, message, "he")
    assert fake_crm.pref_requests == []
    assert message not in caplog.text


# --- a reopened page with a remembered login (POST /auth/session) --------------------

@pytest.mark.parametrize("preferences, expected", [
    (httpx.Response(200, json={"locale": "he", "theme": "dark"}), "he"),
    (httpx.Response(200, json={}), None),
    (httpx.Response(500, json={}), None),
])
def test_a_remembered_login_is_checked_and_the_language_read_again(client, fake_crm, preferences, expected):
    fake_crm.pref_answers.append(preferences)
    response = client.post("/auth/session", headers=TEST_AUTH)
    assert response.status_code == 200
    assert response.json() == {"status": "logged_in", "language": expected}
    assert response.headers["cache-control"] == "no-store"
    assert len(fake_crm.me_requests) == 1 and [r.method for r in fake_crm.pref_requests] == ["GET"]
    assert "theme" not in response.text and fake_crm.requests == []


@pytest.mark.parametrize("me_answer, status", [(httpx.Response(401, json={}), 401), (httpx.Response(500, json={}), 503)])
def test_a_remembered_login_the_crm_refuses_is_not_resumed(client, fake_crm, me_answer, status):
    fake_crm.me_answers.append(me_answer)
    response = client.post("/auth/session", headers=TEST_AUTH)
    assert response.status_code == status
    assert response.json()["logout"] is (status == 401)
    assert fake_crm.pref_requests == []


def test_resuming_needs_a_token(client, fake_crm):
    response = client.post("/auth/session")
    assert response.status_code == 401 and fake_crm.me_requests == [] and fake_crm.pref_requests == []
