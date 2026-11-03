"""Understanding a new request (docs/instructions21.md): app/interpreter.py and the routing in
app/search_chat.py. The model is mocked (fake_ai); the CRM is a mock transport (fake_crm).
Nothing reaches a real AI provider or CRM."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import interpreter
from app.main import app
from tests.test_apartment_chat import ACTIVIST_FIELDS, AUTH, TOKEN, apt, ok
from tests.test_person_chat import FIELDS, V_DATE, V_LOG, V_NOTES, VISIT_FIELDS, contact
from tests.test_search_chat import as_role, found, person, searched

TEFILLIN = "להוסיף תפילין לג'ון"
EMPTY = {"person": None, "field": None, "value": None, "date": None, "notes": None, "question": None, "ask": None}


@pytest.fixture
def client():
    return TestClient(app)


def turn(client, message, language="en", **body):
    response = client.post("/assistant/turn", headers=AUTH,
                           json={"intent": "search", "message": message, "language": language, **body})
    assert response.status_code == 200, response.text
    return response.json()


def crm_calls(fake_crm):
    return [(r.method, r.url.path) for r in fake_crm.requests]


# --- validation ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    {"action": "search", "person": "Cohen"},
    {"action": "search", "person": "כהן"},
    {"action": "create_contact", "person": "Moshe Levi"},
    {"action": "create_contact", "person": "משה לוי"},
    {"action": "edit_contact", "person": "Dana", "field": "phone", "value": "050-1234567"},
    {"action": "edit_contact", "person": "דנה", "field": "טלפון", "value": "050-1234567"},
    {"action": "add_visit", "person": "John", "field": "tefillin", "date": "today"},
    {"action": "add_visit", "person": "ג'ון", "field": "תפילין", "date": "היום", "notes": "שיחה טובה"},
    {"action": "edit_apartment", "person": "Herzl 10 apartment 3", "field": "language", "value": "English"},
    {"action": "edit_apartment", "person": "הרצל 10 דירה 3", "field": "שפה", "value": "אנגלית"},
    {"action": "ask_question", "question": "How do I reset my password?"},
    {"action": "ask_question", "question": "איך מאפסים סיסמה?"},
    {"action": "unclear", "ask": "Record a visit, or change a field?"},
    {"action": "unclear", "ask": "לרשום ביקור, או לשנות שדה?"},
])
def test_every_action_validates(answer):
    full = {**EMPTY, **answer}
    assert interpreter.parse_reply(json.dumps(full, ensure_ascii=False)).model_dump() == full


def test_strings_are_trimmed_empty_is_null_extra_keys_ignored_fences_allowed():
    reply = '```json\n{"action": "search", "person": "  Cohen ", "field": "", "organization_id": 7}\n```'
    assert interpreter.parse_reply(reply).model_dump() == {**EMPTY, "action": "search", "person": "Cohen"}


@pytest.mark.parametrize("reply", [
    "not json",
    "[]",
    '{"action": "delete_contact", "person": "Dana"}',
    '{"person": "Dana"}',
    '{"action": "search", "person": 5}',
    '{"action": "search", "person": ["Dana"]}',
    json.dumps({"action": "search", "person": "x" * 201}),
    json.dumps({"action": "add_visit", "person": "Dana", "notes": "x" * 501}),
    json.dumps({"action": "unclear", "ask": "?" * 201}),
])
def test_unusable_answers_are_failures_never_cut(reply):
    with pytest.raises(ValueError):
        interpreter.parse_reply(reply)


def test_caps_are_inclusive():
    reply = json.dumps({"action": "add_visit", "person": "x" * 200, "value": "v" * 500, "notes": "n" * 500})
    assert interpreter.parse_reply(reply).notes == "n" * 500


@pytest.mark.parametrize("action", ["create_contact", "edit_contact", "add_visit"])
@pytest.mark.parametrize("language, ask", [("en", "Who is it about?"), ("he", "על מי מדובר?")])
def test_a_person_action_without_a_person_becomes_unclear(action, language, ask):
    settled = interpreter.settle(interpreter.Understanding(action=action, field="phone"), language)
    assert settled.action == "unclear" and settled.ask.startswith(ask)


# --- failures: an honest message, nothing searched, nothing started ------------------------

@pytest.mark.parametrize("failure", [
    TimeoutError("timed out"),
    RuntimeError("503 UNAVAILABLE"),
    "not json at all",
    '{"action": "add_person", "person": "תפילין לג\'ון"}',
    json.dumps({"action": "create_contact", "person": "x" * 300}),
])
@pytest.mark.parametrize("language", ["en", "he"])
def test_a_failed_understanding_never_searches_or_starts_anything(client, fake_crm, fake_ai, failure, language):
    fake_ai.understandings.append(failure)
    data = turn(client, TEFILLIN, language=language)
    assert data["outcome"] == "not_understood"
    assert data["reply"].startswith("Sorry, I couldn't understand" if language == "en" else "סליחה, לא הצלחתי")
    assert data["choices"] == [] and "תפילין לג'ון" not in data["reply"]
    assert fake_crm.requests == [] and fake_ai.extraction_requests == []  # no search, no add
    assert data["search_state"]["offer_add"] is False and data["search_state"]["clarify"] is None
    assert len(fake_ai.interpreter_requests) == 1  # no retry


def test_a_missing_key_is_a_failure_too(client, fake_crm, fake_ai, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    data = turn(client, TEFILLIN, language="he")
    assert data["outcome"] == "not_understood" and fake_crm.requests == []
    assert fake_ai.interpreter_requests == []


def test_the_guard_from_the_spec(client, fake_crm, fake_ai):
    """"להוסיף תפילין לג'ון" with a forced provider failure: no search, no person named "תפילין לג'ון"."""
    fake_ai.understandings.append(ConnectionError("down"))
    data = turn(client, TEFILLIN, language="he")
    assert searched(fake_crm) == [] and "תפילין לג'ון" not in json.dumps(data, ensure_ascii=False)


def test_a_failure_is_logged_without_the_message(client, fake_crm, fake_ai, caplog):
    fake_ai.understandings.append("not json")
    turn(client, TEFILLIN, language="he")
    assert "Interpreter: failed" in caplog.text and "תפילין" not in caplog.text


# --- what is sent to the provider -----------------------------------------------------------

def test_only_the_users_text_and_the_language_are_sent(client, fake_crm, fake_ai):
    fake_crm.answers.append(found(people=[person()]))
    turn(client, "Yosef <Cohen>", language="he")
    (request,) = fake_ai.interpreter_requests
    assert request["input"] == "Interface language: Hebrew\n\n<request>\nYosef &lt;Cohen&gt;\n</request>"
    assert request["system_instruction"] == interpreter.INSTRUCTION
    assert request["store"] is False and request["timeout"] == 10
    assert TOKEN not in json.dumps(request, default=str)


# --- clarification --------------------------------------------------------------------------

def test_unclear_asks_and_searches_nothing(client, fake_crm, fake_ai):
    fake_ai.understandings.append({"action": "unclear", "ask": "האם לרשום ביקור אצל ג'ון, או לערוך שדה באיש הקשר?"})
    data = turn(client, TEFILLIN, language="he")
    assert data["outcome"] == "clarify" and data["reply"] == "האם לרשום ביקור אצל ג'ון, או לערוך שדה באיש הקשר?"
    assert fake_crm.requests == [] and fake_ai.extraction_requests == []
    assert data["search_state"]["clarify"] == TEFILLIN


def test_the_two_turn_example_from_the_spec(client, fake_crm, fake_ai):
    fake_ai.understandings += [
        {"action": "unclear", "ask": "האם לרשום ביקור אצל ג'ון, או לערוך שדה באיש הקשר?"},
        {"action": "add_visit", "person": "ג'ון", "field": "תפילין"},
    ]
    first = turn(client, TEFILLIN, language="he")
    fake_crm.answers += [found(people=[person(12, name="ג'ון", last_name="כהן")]), ok(contact())]
    second = turn(client, "ביקור", language="he", search_state=first["search_state"])
    one, two = fake_ai.interpreter_requests
    assert two["input"] == ("Interface language: Hebrew\n\n<request>\nלהוסיף תפילין לג'ון\n</request>\n\n"
                            "<clarification_answer>\nביקור\n</clarification_answer>")
    assert TOKEN not in json.dumps(two, default=str) and "כהן" not in two["input"]  # no CRM records
    assert searched(fake_crm) == ["ג'ון"]
    assert crm_calls(fake_crm)[1:] == [("GET", "/api/assistant/contacts/12")]  # one read
    assert second["intent"] == "person" and second["outcome"] == "visit"
    assert second["state"]["step"] == "visit" and second["state"]["contact_id"] == 12
    assert {r.method for r in fake_crm.requests} == {"GET"}  # nothing saved


@pytest.mark.parametrize("second", [{"action": "unclear", "ask": "?"}, "not json", TimeoutError("slow")])
def test_a_second_unclear_or_failure_ends_with_no_third_call(client, fake_crm, fake_ai, second):
    fake_ai.understandings += [{"action": "unclear", "ask": "Visit or edit?"}, second]
    first = turn(client, "add tefillin to John")
    data = turn(client, "hmm", search_state=first["search_state"])
    assert data["outcome"] == "not_understood" and data["reply"].startswith("I still couldn't tell")
    assert data["search_state"]["clarify"] is None and fake_crm.requests == []
    turn(client, "x")  # one character: answered without any AI call
    assert len(fake_ai.interpreter_requests) == 2


# --- understanding and routing --------------------------------------------------------------

def test_add_visit_searches_only_the_person_never_the_sentence(client, fake_crm, fake_ai):
    fake_ai.understandings.append({"action": "add_visit", "person": "ג'ון", "field": "תפילין"})
    fake_crm.answers.append(found())
    data = turn(client, TEFILLIN, language="he")
    assert searched(fake_crm) == ["ג'ון"]
    assert data["outcome"] == "no_match" and data["choices"] == []  # never an offer to add


def test_a_joined_preposition_is_tried_only_when_the_words_as_written_find_no_one(client, fake_crm, fake_ai):
    fake_ai.understandings.append({"action": "add_visit", "person": "לג'ון"})
    fake_crm.answers += [found(), found(people=[person(12, name="ג'ון")])]
    data = turn(client, TEFILLIN, language="he")
    assert searched(fake_crm) == ["לג'ון", "ג'ון"]
    # Found only without the letter: listed for the user to pick, never opened by itself.
    assert data["intent"] == "search" and [c["action"] for c in data["choices"]] == ["open_person"]
    assert len(fake_crm.requests) == 2


@pytest.mark.parametrize("language, text, answer, query, first_call, intent", [
    ("en", "find Dana Katz", {"action": "search", "person": "Dana Katz"}, "Dana Katz", None, "search"),
    ("he", "חפש את דנה כץ", {"action": "search", "person": "את דנה כץ"}, "דנה כץ", None, "search"),
    ("en", "change Dana's phone to 050-1111111", {"action": "edit_contact", "person": "Dana", "field": "phone",
                                                 "value": "050-1111111"}, "Dana", "/api/assistant/contacts/12", "person"),
    ("he", "תשנה לדנה את הטלפון ל-050-1111111", {"action": "edit_contact", "person": "דנה", "field": "טלפון",
                                                 "value": "050-1111111"}, "דנה", "/api/assistant/contacts/12", "person"),
    ("en", "I visited John today, he put on tefillin", {"action": "add_visit", "person": "John", "field": "tefillin",
                                                         "date": "today"}, "John", "/api/assistant/contacts/12", "person"),
    ("he", "ביקרתי היום אצל ג'ון", {"action": "add_visit", "person": "ג'ון", "date": "היום"}, "ג'ון",
     "/api/assistant/contacts/12", "person"),
])
def test_each_clear_action_starts_the_right_flow_with_the_right_words(client, fake_crm, fake_ai, language, text,
                                                                      answer, query, first_call, intent):
    fake_ai.understandings.append(answer)
    fake_crm.answers += [found(people=[person(12)]), ok(contact()), ok(contact())]
    data = turn(client, text, language=language)
    assert searched(fake_crm) == [query]
    if first_call:
        assert crm_calls(fake_crm)[1] == ("GET", first_call)
    assert data["intent"] == intent
    if answer["action"] == "edit_contact":  # this person has no phone field: the user picks one
        assert data["outcome"] == "choose_field" and answer["field"] in data["reply"]
    assert {r.method for r in fake_crm.requests} == {"GET"}


@pytest.mark.parametrize("language, text, answer, query", [
    ("en", "set apartment 3 at Herzl 10 to English", {"action": "edit_apartment", "person": "Herzl 10 apartment 3",
                                                      "value": "English"}, "Herzl 10 apartment 3"),
    ("he", "שנה את השפה בהרצל 10 דירה 3 לאנגלית", {"action": "edit_apartment", "person": "הרצל 10 דירה 3",
                                                   "value": "אנגלית"}, "הרצל 10 דירה 3"),
])
def test_edit_apartment_opens_the_one_match(client, fake_crm, fake_ai, language, text, answer, query):
    fake_ai.understandings.append(answer)
    fake_crm.answers += [found(apartments=[apt(57797, 35852)]), ok(apt(57797, 35852, fields=ACTIVIST_FIELDS))]
    data = turn(client, text, language=language)
    assert searched(fake_crm) == [query] and data["intent"] == "edit_apartment"
    assert crm_calls(fake_crm)[1] == ("GET", "/api/assistant/apartments/getapartment")


@pytest.mark.parametrize("language, text, answer", [
    ("en", "Dana Levi, phone 050-1234567", {"action": "create_contact", "person": "Dana Levi"}),
    ("he", "תוסיף את רחל כהן, היא גרה ברחוב הגפן 5", {"action": "create_contact", "person": "רחל כהן"}),
])
def test_create_contact_searches_the_name_then_reads_the_details_from_the_same_message(client, fake_crm, fake_ai,
                                                                                      language, text, answer):
    fake_ai.understandings.append(answer)
    fake_ai.extractions.append({"first_name": "x"})
    fake_crm.answers += [found(), httpx.Response(200, json={"status": "needs_information", "missing_fields": ["last_name"],
                                                            "invalid_fields": [], "fields": {"first_name": "x"}})]
    data = turn(client, text, language=language)
    assert searched(fake_crm) == [answer["person"]] and data["intent"] == "create_contact"
    assert text in fake_ai.extraction_requests[0]["input"]


@pytest.mark.parametrize("language, question", [("en", "How do I reset my password?"),
                                                ("he", "איך מאפסים סיסמה?")])
def test_a_question_goes_to_the_guides_not_to_the_crm(client, fake_crm, fake_ai, sample_knowledge, language, question):
    fake_ai.understandings.append({"action": "ask_question", "question": question})
    data = turn(client, question, language=language)
    assert data["intent"] == "ask_question" and data["outcome"] == "answered" and data["passages"]
    assert fake_crm.requests == []


# --- follow-ups and permissions --------------------------------------------------------------

def test_follow_ups_never_reach_the_provider(client, fake_crm, fake_ai):
    turn(client, "yes", search_state={"offer_add": True, "language": "en"})
    turn(client, "no", search_state={"offer_add": True, "language": "en"})
    fake_crm.answers.append(ok(contact()))
    client.post("/assistant/turn", headers=AUTH, json={
        "intent": "person", "message": "[edit]",
        "person_state": {"step": "menu", "language": "en", "contact_id": 12}})
    fake_crm.answers.append(ok(apt(57797, 35852, fields=ACTIVIST_FIELDS)))
    client.post("/assistant/turn", headers=AUTH, json={
        "intent": "edit_apartment", "message": "1",
        "apartment_state": {"step": "choose_field", "language": "en", "apartment_id": 57797, "lineage_id": 35852}})
    assert fake_ai.interpreter_requests == []


@pytest.mark.parametrize("role", ["activist", "campaign_manager", "shliach", None])
@pytest.mark.parametrize("action", ["edit_contact", "add_visit"])
def test_other_roles_get_no_contact_access_whatever_the_model_says(client, fake_crm, fake_ai, role, action):
    as_role(fake_crm, role)
    fake_ai.understandings.append({"action": action, "person": "Dana"})
    data = turn(client, "change Dana's phone")
    assert data["outcome"] == "forbidden" and fake_crm.requests == []


# --- the user's words matched against the CRM's own fields (section 6) ------------------------

def understood_person(client, fake_crm, fake_ai, answer, view=None, language="en"):
    fake_ai.understandings.append(answer)
    fake_crm.answers += [found(people=[person(12)]), ok(view or contact())]
    return turn(client, "a request", language=language)


def test_a_named_field_with_a_supported_value_goes_straight_to_the_confirm_screen(client, fake_crm, fake_ai):
    data = understood_person(client, fake_crm, fake_ai, {"action": "edit_contact", "person": "Yosef",
                                                         "field": "email", "value": "yosef@example.org"})
    assert data["outcome"] == "confirm"
    assert "Change Email of Yosef Cohen:\nfrom: -\nto: yosef@example.org" in data["reply"]
    assert data["state"]["pending"]["value"] == "yosef@example.org"
    assert {r.method for r in fake_crm.requests} == {"GET"}  # a yes is still needed


@pytest.mark.parametrize("field, value, sent", [
    ("status", "warm", "warm"),            # an option, by its label
    ("the status", "Warm", "warm"),
    ("keeps shabbat", "yes", True),
    ("birthday", "2/3/1981", "1981-03-02"),
    ("priority score", "7", "7"),
])
def test_each_kind_of_value_resolves_to_what_the_crm_supports(client, fake_crm, fake_ai, field, value, sent):
    data = understood_person(client, fake_crm, fake_ai, {"action": "edit_contact", "person": "Yosef",
                                                         "field": field, "value": value})
    assert data["outcome"] == "confirm" and data["state"]["pending"]["value"] == sent


@pytest.mark.parametrize("field, value", [
    ("status", "hot"),          # no such option
    ("status", "2"),            # a number in a sentence is not option 2
    ("keeps shabbat", "maybe"),
    ("birthday", "next sunday"),
    ("priority score", "high"),
    ("email", "[clear]"),        # never a clear from free text
])
def test_a_value_the_crm_does_not_support_is_asked_about_with_the_real_choices(client, fake_crm, fake_ai, field, value):
    data = understood_person(client, fake_crm, fake_ai, {"action": "edit_contact", "person": "Yosef",
                                                         "field": field, "value": value})
    assert data["outcome"] == "choose_value" and f'I couldn\'t use "{value}"' in data["reply"]
    if field == "status":
        assert [c["label"] for c in data["choices"]][:2] == ["1. New", "2. Warm"]


@pytest.mark.parametrize("field", ["name", "mobile", "favourite colour"])
def test_a_field_that_is_not_exactly_one_crm_field_is_chosen_from_the_list(client, fake_crm, fake_ai, field):
    data = understood_person(client, fake_crm, fake_ai, {"action": "edit_contact", "person": "Yosef",
                                                         "field": field, "value": "x"})
    assert data["outcome"] == "choose_field" and f'which field "{field}" is' in data["reply"]


def test_hebrew_field_words_match_without_the_joined_article(client, fake_crm, fake_ai):
    he_phone = {"field_key": "contacts.phone", "label": "טלפון", "kind": "text", "current_value": "050-0000000"}
    data = understood_person(client, fake_crm, fake_ai, {"action": "edit_contact", "person": "יוסף",
                                                         "field": "הטלפון", "value": "052-7654321"},
                             view=contact(fields=[he_phone], name="יוסף", last_name="כהן"), language="he")
    assert "שינוי טלפון של יוסף כהן:\nמ: 050-0000000\nל: 052-7654321" in data["reply"]


def test_a_visit_starts_with_what_the_user_said_ticked_and_shown(client, fake_crm, fake_ai):
    data = understood_person(client, fake_crm, fake_ai, {
        "action": "add_visit", "person": "Yosef", "field": "tefillin", "date": "today",
        "notes": "he also lit Shabbat candles"})
    assert data["outcome"] == "visit"
    assert "From your message: Date: 2026-11-02, Tefillin ✓, Shabbat candles ✓." in data["reply"]
    assert data["prompt"].splitlines()[-1].startswith("What was done?")  # the date is not asked again
    assert data["state"]["visit"]["prefilled"] == ["visits.visit_date"]
    assert {r.method for r in fake_crm.requests} == {"GET"}


def turn_person(client, message, state):
    response = client.post("/assistant/turn", headers=AUTH,
                           json={"intent": "person", "message": message, "person_state": state})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_mitzvot_from_the_message_come_ticked_and_reach_the_confirm_screen(client, fake_crm, fake_ai):
    data = understood_person(client, fake_crm, fake_ai, {"action": "add_visit", "person": "Yosef",
                                                         "field": "tefillin", "date": "today"})
    state = data["state"]  # the date came from the message: the first question is what was done
    assert [c["label"] for c in data["choices"]] == ["✓ Tefillin", "Shabbat candles", "Tzedaka", "Continue"]
    assert "1. Tefillin ✓" in data["reply"]  # for a text channel
    fake_crm.answers.append(ok(contact()))
    confirm = turn_person(client, "[finish]", state)
    assert "- Date: 2026-11-02" in confirm["reply"] and "- Tefillin ✓" in confirm["reply"]
    assert {r.method for r in fake_crm.requests} == {"GET"}


def test_a_visit_for_a_draft_shows_the_person_and_says_why_not(client, fake_crm, fake_ai):
    data = understood_person(client, fake_crm, fake_ai, {"action": "add_visit", "person": "Yosef"},
                             view=contact(status="draft"))
    assert data["outcome"] == "menu" and "once this contact is approved" in data["reply"]


def test_an_apartment_field_and_value_go_to_its_confirm_screen(client, fake_crm, fake_ai):
    fake_ai.understandings.append({"action": "edit_apartment", "person": "Herzl 10", "field": "jewish status",
                                   "value": "Jewish"})
    fake_crm.answers += [found(apartments=[apt(57797, 35852)]), ok(apt(57797, 35852, fields=ACTIVIST_FIELDS))]
    data = turn(client, "Herzl 10 is a Jewish home")
    assert data["intent"] == "edit_apartment" and data["outcome"] in ("confirm", "choose_value")
    assert {r.method for r in fake_crm.requests} == {"GET"}


def test_a_crm_without_the_contact_routes_says_so_plainly(client, fake_crm, fake_ai):
    fake_ai.understandings.append({"action": "edit_contact", "person": "Yosef"})
    fake_crm.answers += [found(people=[person(12)]), httpx.Response(404, json={"detail": "Not Found"})]
    data = turn(client, "change Yosef")
    assert data["outcome"] == "contacts_missing"
    assert data["reply"] == "Opening a person in the chat isn't available on this CRM yet. Nothing was changed."


def test_a_contact_the_crm_will_not_show_is_still_gone(client, fake_crm, fake_ai):
    fake_ai.understandings.append({"action": "edit_contact", "person": "Yosef"})
    fake_crm.answers += [found(people=[person(12)]), httpx.Response(404, json={"detail": "Contact not found"})]
    assert turn(client, "change Yosef")["outcome"] == "gone"

# --- a new contact: the add endpoint's basic details, then every field the CRM allows -------

def test_after_saving_a_new_contact_every_editable_field_is_offered(client, fake_crm):
    from tests.test_contact_chat import DANA, created, shown_proposal_state
    fake_crm.answers += [created(DANA), ok(contact())]
    data = client.post("/assistant/turn", headers=AUTH, json={
        "intent": "create_contact", "message": "yes", "state": shown_proposal_state(DANA)}).json()
    assert [r.url.path for r in fake_crm.requests][-1] == "/api/assistant/contacts/42"
    assert data["intent"] == "person" and data["outcome"] == "choose_field"
    assert data["reply"].startswith("Saved as a draft contact (#42):")
    assert "You can fill in more of this person's details now:" in data["reply"]
    assert [c["label"] for c in data["choices"]][:3] == ["1. First name", "2. Last name", "3. Email"]
    assert data["state"]["contact_id"] == 42


def test_when_the_new_contact_cannot_be_opened_the_save_is_still_reported(client, fake_crm):
    from tests.test_contact_chat import DANA, created, shown_proposal_state
    fake_crm.answers += [created(DANA), httpx.Response(404, json={"detail": "Not Found"})]
    data = client.post("/assistant/turn", headers=AUTH, json={
        "intent": "create_contact", "message": "yes", "state": shown_proposal_state(DANA)}).json()
    assert data["intent"] == "create_contact" and data["outcome"] == "created"
    assert data["reply"].startswith("Saved as a draft contact (#42):")

# --- code decides between a visit and a detail of the person (live test, 3 Nov) ---------------

MEZUZA = {"field_key": "customfield.contact_mekurav.mezuza", "label": "Mezuza", "kind": "text", "current_value": None}
JEWISH_NAME = {"field_key": "customfield.contact_mekurav.jewish_name", "label": "Jewish Name:", "kind": "text",
               "current_value": "Tzvi"}


def test_add_mezuza_to_zvi_edits_the_persons_mezuza_when_visits_dont_record_it(client, fake_crm, fake_ai):
    """The live case: the model said add_visit, but in this CRM Mezuza is only a contact field."""
    calls_only = {**V_LOG, "options": [{"value": "call", "label": "Call"}]}  # no mezuza among the interactions
    view = contact(fields=FIELDS + [MEZUZA], visit_fields=[V_DATE, V_NOTES, calls_only])
    data = understood_person(client, fake_crm, fake_ai, {"action": "add_visit", "person": "zvi", "field": "mezuza"},
                             view=view)
    assert data["outcome"] == "choose_value" and data["state"]["field_key"] == MEZUZA["field_key"]
    assert '"mezuza" is a detail of the person, not part of a visit, so here it is to change:' in data["reply"]
    assert data["state"]["visit"] is None


def test_a_visit_item_named_as_an_edit_starts_a_visit_with_it_ticked(client, fake_crm, fake_ai):
    data = understood_person(client, fake_crm, fake_ai, {"action": "edit_contact", "person": "zvi",
                                                         "field": "mezuza check"})
    assert data["outcome"] == "visit"
    assert '"mezuza check" is recorded with a visit, so here is a new visit:' in data["reply"]
    assert "From your message: Mezuza check ✓." in data["reply"]


def test_an_item_both_lists_have_follows_the_models_reading(client, fake_crm, fake_ai):
    tefillin = {"field_key": "customfield.contact_mekurav.tefillin", "label": "Tefillin", "kind": "text",
                "current_value": None}
    data = understood_person(client, fake_crm, fake_ai, {"action": "add_visit", "person": "zvi", "field": "tefillin"},
                             view=contact(fields=FIELDS + [tefillin]))
    assert data["outcome"] == "visit" and "Tefillin ✓" in data["reply"]


def test_named_interactions_are_pre_ticked_and_sent(client, fake_crm, fake_ai):
    data = understood_person(client, fake_crm, fake_ai, {"action": "add_visit", "person": "zvi",
                                                         "notes": "we did the mezuza check", "date": "today"},
                             view=contact(visit_fields=[V_DATE, V_LOG, V_NOTES]))
    assert data["prompt"].splitlines()[-1].startswith("Interactions - what happened")
    assert [c["label"] for c in data["choices"]] == ["Call", "✓ Mezuza check", "Continue"]
    assert "2. Mezuza check ✓" in data["reply"]
    view = contact(visit_fields=[V_DATE, V_LOG, V_NOTES])
    fake_crm.answers += [ok(view), ok(view), ok(view)]
    q = turn_person(client, "[next]", data["state"])      # Continue keeps the tick
    confirm = turn_person(client, "[finish]", q["state"])
    assert "- Interactions: Mezuza check" in confirm["reply"]
    fake_crm.answers += [httpx.Response(201, json={"status": "created", "replayed": False, "visit_id": 9,
                                                   "contact_id": 12, "visit": None}), ok(view)]
    turn_person(client, "yes", confirm["state"])
    (post,) = [r for r in fake_crm.requests if r.method == "POST"]
    sent = json.loads(post.content)["values"]
    assert sent == {"visits.visit_date": "2026-11-02", "visits.interactions": [{"category": "mezuza", "note": ""}]}


def test_a_label_ending_in_a_colon_is_shown_once(client, fake_crm, fake_ai):
    data = understood_person(client, fake_crm, fake_ai, {"action": "edit_contact", "person": "zvi"},
                             view=contact(fields=[NAME_ONLY, JEWISH_NAME]))
    assert "- Jewish Name: Tzvi" in data["reply"] and "Jewish Name::" not in data["reply"]


NAME_ONLY = {"field_key": "contacts.name", "label": "First name", "kind": "text", "current_value": "Zvi"}