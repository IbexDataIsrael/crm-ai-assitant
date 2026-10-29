"""The user's language (docs/instructions19.md): read at login, saved on a change,
the conservative last-used-language rule, and every reply in that language.

CRM and model are always mocks (fake_crm / fake_gemini in conftest.py).
"""

import importlib
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app import field_extraction, generation, knowledge
from app import language as user_language
from app.language import LanguageState, clear_language, observe
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


def session(language="en", **values):
    return LanguageState(language=language, **values).model_dump()


def ask(client, message, state=None, intent="ask_question"):
    body = {"intent": intent, "message": message}
    if state is not None:
        body["language_state"] = state
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


# --- POST /assistant/language (the toggle's save) ----------------------------------

def test_saving_the_language_needs_a_login(client, fake_crm):
    response = client.post("/assistant/language", json={"language": "he"})
    assert response.status_code == 401 and response.json()["logout"] is True
    assert fake_crm.pref_requests == [] and fake_crm.me_requests == []


@pytest.mark.parametrize("value", ["he", "en"])
def test_a_valid_language_is_saved_with_exactly_one_put(client, fake_crm, value):
    response = client.post("/assistant/language", json={"language": value}, headers=TEST_AUTH)
    assert response.status_code == 200
    assert response.json() == {"status": "saved", "language": value,
                               "reply": "השפה נשמרה." if value == "he" else "Language saved."}
    assert fake_crm.puts() == [{"key": "locale", "value": value}]
    assert len(fake_crm.me_requests) == 1  # the same login check as the chat


@pytest.mark.parametrize("body", [{"language": "fr"}, {"language": "HE"}, {"language": None}, {"language": 1},
                                  {}, {"language": "he", "user_id": 5}, ["he"]])
def test_any_other_value_is_refused_and_calls_nothing(client, fake_crm, body):
    response = client.post("/assistant/language", json=body, headers=TEST_AUTH)
    assert response.status_code == 400 and response.json()["status"] == "invalid_language"
    assert fake_crm.pref_requests == [] and fake_crm.me_requests == [] and fake_crm.requests == []


@pytest.mark.parametrize("failure", [httpx.Response(500, json={"detail": "x"}), httpx.ConnectError("refused"),
                                     httpx.ReadTimeout("slow"), httpx.Response(400, json={"detail": "Invalid language"})])
def test_a_failed_save_is_an_error_but_the_next_turn_still_works(client, fake_crm, searched, fake_gemini, failure):
    fake_crm.pref_answers.append(failure)
    response = client.post("/assistant/language", json={"language": "he"}, headers=TEST_AUTH)
    assert response.status_code == 502
    assert response.json() == {"status": "not_saved", "language": "he",
                               "reply": "לא הצלחתי לשמור את השפה. היא תישאר לשיחה הזו."}
    # The page keeps Hebrew for the session and marks the one retry; the turn is answered.
    fake_gemini.reply = FakeInteraction("מערכת CRM [1].")
    data = ask(client, "מה זה?", session("he", toggled=True, saved="en", retry="he"))
    assert data["outcome"] == "answered" and data["language_state"]["saved"] == "he"
    assert fake_crm.puts() == [{"key": "locale", "value": "he"}] * 2


def test_a_crm_401_on_save_logs_out(client, fake_crm):
    fake_crm.pref_answers.append(httpx.Response(401, json={"detail": "Could not validate credentials"}))
    response = client.post("/assistant/language", json={"language": "en"}, headers=TEST_AUTH)
    assert response.status_code == 401 and response.json()["logout"] is True


# --- detection: which messages are "clearly" one language --------------------------

@pytest.mark.parametrize("text, expected", [
    ("איך מוסיפים איש קשר חדש?", "he"),                                    # pure Hebrew
    ("How do I add a new contact?", "en"),                                    # pure English
    ("hello שלום עולם", None),                                               # 5 Latin / 8 Hebrew: 62% -> unclear
    ("contacts אנשי קשר", None),                                             # 8 Latin / 7 Hebrew
    ("ok", None), ("hi!", None), ("מה?", None),                              # under 4 letters
    ("0521234567", None), ("12/10/2026 14:30", None), ("?!...", None),       # digits / punctuation only
    ("find", None), ("Please update", None), ("הוסף", None), ("בבקשה עדכן", None),  # commands only
    ("yes", None), ("כן", None), ("cancel", None), ("ביטול", None),          # confirm words never count
    ("How do I add a contact named משה כהן to the list?", "en"),             # a Hebrew name in English
    ("איך מוסיפים את Dana לרשימה?", "he"),                                   # an English name in Hebrew
    ("ראיתי את זה ב-https://example.com/how/to/add/new/contacts/page אתמול", "he"),  # URL removed
    ("שלחו את המסמך ל-dana.levi.example@example.org מחר", "he"),             # email removed
    ("please write to רחל@example.org about the meeting", "en"),
    ("find דנה לוי", "he"),                                                  # verb dropped, a name is left
])
def test_clear_language(text, expected):
    assert clear_language(text) == expected


def test_field_values_are_removed_before_counting():
    # The contact flow passes the values it read; what is left decides.
    assert clear_language("Add דנה לוי 0521234567", ignore=["דנה", "לוי", "0521234567"]) is None
    assert clear_language("Add דנה לוי, she lives in תל אביב", ignore=["דנה", "לוי", "תל אביב"]) == "en"


def test_one_clear_message_switches_when_the_toggle_was_not_pressed():
    state = observe(LanguageState(language="en"), "איך מוסיפים איש קשר?")
    assert state.language == "he" and state.streak == 0


def test_after_a_toggle_press_two_clear_messages_in_a_row_are_needed():
    state = LanguageState(language="en", toggled=True)
    observe(state, "איך מוסיפים איש קשר?")
    assert state.language == "en" and state.streak == 1
    observe(state, "ומה עם תרומות?")
    assert state.language == "he" and state.streak == 0


def test_a_clear_message_in_the_current_language_breaks_the_row_and_unclear_ones_dont_count():
    state = LanguageState(language="en", toggled=True)
    observe(state, "איך מוסיפים איש קשר?")
    observe(state, "ok")                          # unclear: neither counts nor resets
    assert state.streak == 1
    observe(state, "How do I add a contact?")     # clear English: the row is broken
    assert state.streak == 0
    observe(state, "ומה עם תרומות?")
    assert state.language == "en" and state.streak == 1


def test_a_toggle_press_resets_the_row():
    # The page sends toggled=True, streak=0 after a press (static/index.html toggleLanguage).
    state = LanguageState(language="he", toggled=True, streak=0)
    observe(state, "How do I add a contact?")
    assert state.language == "he"


# --- turns: switch, save once, retry once -----------------------------------------

def test_a_clear_message_in_the_other_language_switches_saves_once_and_answers_in_it(
        client, fake_crm, searched, fake_gemini):
    fake_gemini.reply = FakeInteraction("מערכת CRM דו-לשונית [1].")
    data = ask(client, "מה זה נקודות יהודיות?", session("en", saved="en"))
    assert data["language_state"] == session("he", saved="he")
    assert data["language"] == "he" and data["reply"].startswith("תשובה שנכתבה ב-AI")
    assert data["language_notice"] == "השפה נשמרה."
    assert fake_crm.puts() == [{"key": "locale", "value": "he"}]
    # The same language again: nothing more is written.
    again = ask(client, "ומה עם תרומות?", data["language_state"])
    assert again["language_notice"] is None and len(fake_crm.puts()) == 1


@pytest.mark.parametrize("saved", ["en", None])
def test_no_save_without_a_change(client, fake_crm, searched, fake_gemini, saved):
    # saved=None: the CRM had no value and the page uses the browser's language - never saved by itself.
    fake_gemini.reply = FakeInteraction("A bilingual CRM [2].")
    data = ask(client, "What is this system?", session("en", saved=saved))
    assert data["language_state"]["language"] == "en" and data["language_notice"] is None
    assert fake_crm.pref_requests == []


def test_a_failed_save_keeps_the_language_and_is_retried_once_on_the_next_turn(
        client, fake_crm, searched, fake_gemini):
    fake_gemini.reply = FakeInteraction("מערכת CRM [1].")
    fake_crm.pref_answers += [httpx.Response(500, json={}), httpx.Response(200, json={"locale": "he"})]
    first = ask(client, "מה זה נקודות יהודיות?", session("en", saved="en"))
    assert first["outcome"] == "answered" and first["language_state"]["language"] == "he"
    assert first["language_state"]["retry"] == "he" and first["language_state"]["saved"] == "en"
    assert first["language_notice"] == "לא הצלחתי לשמור את השפה. היא תישאר לשיחה הזו."
    second = ask(client, "ומה עם תרומות?", first["language_state"])  # the one retry
    assert second["language_state"] == session("he", saved="he")
    assert second["language_notice"] == "השפה נשמרה."
    ask(client, "ואיך מוסיפים איש קשר?", second["language_state"])
    assert fake_crm.puts() == [{"key": "locale", "value": "he"}] * 2  # never the same value a third time


def test_a_retry_that_fails_again_is_not_repeated(client, fake_crm, searched, fake_gemini):
    fake_gemini.reply = FakeInteraction("מערכת CRM [1].")
    fake_crm.pref_answers += [httpx.ConnectError("x"), httpx.ConnectError("x")]
    first = ask(client, "מה זה נקודות יהודיות?", session("en", saved="en"))
    second = ask(client, "ומה עם תרומות?", first["language_state"])
    third = ask(client, "ואיך מוסיפים איש קשר?", second["language_state"])
    assert second["outcome"] == third["outcome"] == "answered"
    assert third["language_state"]["language"] == "he" and third["language_state"]["retry"] is None
    assert len(fake_crm.puts()) == 2


def test_a_session_without_language_state_starts_from_the_sent_language(client, fake_crm, searched, fake_gemini):
    fake_gemini.reply = FakeInteraction("A bilingual CRM [2].")
    response = client.post("/assistant/turn", json={"intent": "ask_question", "message": "What is this?",
                                                    "language": "en"}, headers=TEST_AUTH)
    assert response.json()["language_state"] == session("en")


def test_the_language_state_refuses_extra_keys(client, fake_crm):
    response = client.post("/assistant/turn", headers=TEST_AUTH, json={
        "intent": "ask_question", "message": "", "language_state": {**session("he"), "message_text": "x"}})
    assert response.status_code == 422


# --- what never changes the language ------------------------------------------------

@pytest.mark.parametrize("intent", ["create_contact", "edit_apartment", "ask_question", "update_contact"])
def test_a_menu_choice_never_changes_the_language(client, fake_crm, intent):
    data = ask(client, "", session("he", saved="he"), intent=intent)
    assert data["language_state"] == session("he", saved="he")
    assert re.search(r"[֐-׿]", data["reply"])
    assert fake_crm.pref_requests == []


def test_a_hebrew_street_name_in_an_english_apartment_session_changes_nothing(client, fake_crm):
    fake_crm.answers.append(httpx.Response(200, json={"results": [], "truncated": False}))
    data = ask(client, "הרצל 10 תל אביב יפו", session("en", saved="en"), intent="edit_apartment")
    assert data["reply"].startswith("I found no apartment")
    assert data["language_state"] == session("en", saved="en") and fake_crm.pref_requests == []


@pytest.mark.parametrize("message", ["כן", "yes", "לא", "1", "[clear]", "מרכז העיר הישן של ירושלים"])
def test_no_apartment_flow_message_changes_the_language(client, fake_crm, message):
    # Addresses, picks, values and confirm words: whatever the script, the language stays.
    fake_crm.answers.append(httpx.Response(200, json={"results": [], "truncated": False}))
    data = ask(client, message, session("en"), intent="edit_apartment")
    assert data["language_state"]["language"] == "en"


def test_field_values_in_the_contact_flow_never_switch(client, fake_crm, monkeypatch):
    def extract(message, asked):
        return field_extraction.Extraction("ok", fields={"first_name": "דנה", "last_name": "לוי",
                                                         "phone": "0521234567"})
    monkeypatch.setattr(field_extraction, "extract_fields", extract)
    fake_crm.answers.append(httpx.Response(200, json={
        "status": "needs_information", "missing_fields": [], "invalid_fields": [],
        "fields": {"first_name": "דנה", "last_name": "לוי", "phone": "0521234567", "email": None, "address": None}}))
    data = ask(client, "Add דנה לוי 0521234567", session("en", saved="en"), intent="create_contact")
    assert data["language_state"]["language"] == "en" and fake_crm.pref_requests == []


def test_a_free_hebrew_sentence_in_the_contact_flow_switches(client, fake_crm, monkeypatch):
    monkeypatch.setattr(field_extraction, "extract_fields",
                        lambda message, asked: field_extraction.Extraction("ok", fields={}))
    data = ask(client, "אני רוצה להוסיף מישהו חדש למערכת", session("en", saved="en"), intent="create_contact")
    assert data["language_state"]["language"] == "he"
    assert data["reply"].startswith("לא מצאתי פרטי איש קשר")
    assert fake_crm.puts() == [{"key": "locale", "value": "he"}]


# --- Ask: both guides searched, the answer in the user's language -------------------

@pytest.mark.parametrize("message, language", [("What is this system?", "en"), ("מה זה המערכת?", "he"),
                                               ("What is this system?", "he")])
def test_ask_searches_both_guides_and_names_the_users_language(
        client, fake_crm, searched, fake_gemini, message, language):
    # The last case: an English question in a Hebrew session after a toggle press (one English
    # message doesn't switch it), so the answer must still be written in Hebrew.
    fake_gemini.reply = FakeInteraction("[1]")
    data = ask(client, message, session(language, toggled=True, saved=language))
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

TEXT_MODULES = ("app.main", "app.contact_chat", "app.apartment_chat", "app.knowledge", "app.crm", "app.language",
                "app.generation")


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
                     "app.knowledge.TEXTS", "app.crm.ADD_CONTACT_POINTER", "app.language.NOTICES['saved']"):
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
    data = ask(client, "מה זה?", session("he", saved="he"))
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
        "intent": "ask_question", "message": "x", "language_state": session("he")})
    assert response.status_code == status
    assert re.search(r"[֐-׿]", response.json()["reply"]) and not _english_left(response.json()["reply"])


def test_hebrew_contact_and_apartment_starts_have_no_english(client, fake_crm):
    for intent in ("create_contact", "edit_apartment", "update_contact"):
        data = ask(client, "", session("he"), intent=intent)
        assert _english_left(data["reply"]) == [], (intent, data["reply"])


def test_privacy_only_the_two_letter_value_goes_to_the_crm(client, fake_crm, searched, fake_gemini, caplog):
    fake_gemini.reply = FakeInteraction("מערכת CRM [1].")
    message = "מה זה נקודות יהודיות ומי משתמש בה?"
    data = ask(client, message, session("en", saved="en"))
    assert [json.loads(r.content) for r in fake_crm.pref_requests] == [{"key": "locale", "value": "he"}]
    assert message not in json.dumps(data["language_state"], ensure_ascii=False)
    assert message not in caplog.text
