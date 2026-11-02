"""The free-text start (docs/instructions20.md): POST /assistant/turn, intent search.

The CRM is a mock transport (fake_crm); nothing reaches a real CRM or AI
provider. The search answer copies the shape of sheepsidebay_crm
backend/app/routers/assistant_search.py (fixes117); apartments are the same
objects as the apartment search (with editable_fields), opened with
GET /api/assistant/apartments/getapartment?id=<lineage id> (fixes118).
"""

import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app import search_chat
from app.main import app
from tests.conftest import CRM_ME
from tests.test_apartment_chat import ACTIVIST_FIELDS, AUTH, TOKEN, apt, ok
from tests.test_contact_chat import proposal

SEARCH_PATH = "/api/assistant/search"
HEBREW = re.compile(r"[֐-׿]")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def ai(fake_ai):
    """The interpreter's model is mocked; unless a test queues an answer it reads every
    request as a plain search for the text as typed (see tests/test_interpreter.py)."""
    return fake_ai


def person(contact_id=5, name="Yosef", last_name="Cohen", address="Herzl 10, Tel Aviv", **values):
    return {"id": contact_id, "name": name, "last_name": last_name, "phone": "0521234567",
            "address": address, "status": "active", "status_label": None, **values}


def found(people=(), apartments=(), people_truncated=False, apartments_truncated=False, can_add=True):
    return ok({"people": list(people), "apartments": list(apartments), "people_truncated": people_truncated,
               "apartments_truncated": apartments_truncated, "can_add_person": can_add})


def as_role(fake_crm, role):
    """The next login check answers with this role (None = the key is missing)."""
    me = {k: v for k, v in CRM_ME.items() if k != "role"}
    if role is not None:
        me["role"] = role
    fake_crm.me_answers.append(httpx.Response(200, json=me))


def turn(client, message="", language="en", **body):
    response = client.post("/assistant/turn", headers=AUTH,
                           json={"intent": "search", "message": message, "language": language, **body})
    assert response.status_code == 200, response.text
    return response.json()


def searched(fake_crm):
    return [r.url.params["q"] for r in fake_crm.requests if r.url.path == SEARCH_PATH]


# --- the search request ----------------------------------------------------------------

@pytest.mark.parametrize("message", ["", " ", "a", " ב ", "7"])
def test_less_than_two_characters_is_not_searched(client, fake_crm, message):
    data = turn(client, message)
    assert data["outcome"] == "too_short" and "at least 2" in data["reply"]
    assert fake_crm.requests == []


def test_the_text_goes_to_the_combined_search_with_the_users_token(client, fake_crm):
    fake_crm.answers.append(found())
    turn(client, "  Cohen  ")
    request = fake_crm.requests[0]
    assert request.method == "GET" and request.url.path == SEARCH_PATH
    assert request.url.params["q"] == "Cohen"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert str(request.url).startswith("http://crm.test:8001/")


def test_results_count_first_then_people_and_apartments_with_ids(client, fake_crm):
    fake_crm.answers.append(found(
        people=[person(5), person(6, name="Yosefa", address=None, status="draft", status_label="Draft")],
        apartments=[apt(57797, 35852)]))
    data = turn(client, "Cohen")
    assert data["outcome"] == "results"
    assert data["prompt"] == "I found 2 people and 1 apartment."
    reply = data["reply"].split("\n")
    assert reply[1] == "People:" and reply[4] == "Apartments:"
    assert reply[2] == "- Yosef Cohen - Herzl 10, Tel Aviv (#5)"
    assert reply[3] == "- Yosefa Cohen [Draft] (#6)"
    assert reply[5] == "- Herzl 10, Tel Aviv, apartment 3, floor 1 - Cohen"  # never the internal id
    assert [(c["action"], c["id"], c["group"]) for c in data["choices"]] == [
        ("open_person", 5, "people"), ("open_person", 6, "people"), ("open_apartment", 35852, "apartments")]
    assert data["search_state"] == {"offer_add": False, "language": "en", "add_text": None, "clarify": None}


@pytest.mark.parametrize("people, apartments, expected", [
    (1, 0, "מצאתי אדם אחד."),
    (2, 0, "מצאתי 2 אנשים."),
    (0, 1, "מצאתי דירה אחת."),
    (2, 1, "מצאתי 2 אנשים ודירה אחת."),
    (1, 3, "מצאתי אדם אחד ו-3 דירות."),
])
def test_hebrew_count_lines(client, fake_crm, people, apartments, expected):
    fake_crm.answers.append(found(people=[person(i + 1) for i in range(people)],
                                  apartments=[apt(i + 10, i + 10) for i in range(apartments)]))
    data = turn(client, "כהן", language="he")
    assert data["prompt"] == expected
    assert "אנשים:" in data["reply"] or not people


def test_truncated_lists_say_so(client, fake_crm):
    fake_crm.answers.append(found(people=[person(1), person(2)], apartments=[apt()],
                                  people_truncated=True, apartments_truncated=True))
    reply = turn(client, "Cohen")["reply"]
    assert "(Only the first 2 are shown" in reply and "(Only the first 1 are shown" in reply


def test_a_search_never_writes(client, fake_crm):
    fake_crm.answers += [found(people=[person()]), found(), found(apartments=[apt()])]
    for text in ("Cohen", "nobody", "Herzl"):
        turn(client, text)
    assert {r.method for r in fake_crm.requests} == {"GET"}
    assert fake_crm.puts() == []


# --- permissions ---------------------------------------------------------------------

@pytest.mark.parametrize("role", ["shliach", "activist", "campaign_manager", "superadmin", "", None])
def test_people_are_never_shown_to_other_roles_even_if_the_crm_sends_them(client, fake_crm, role):
    as_role(fake_crm, role)
    fake_crm.answers.append(found(people=[person()], apartments=[apt()], can_add=True))
    data = turn(client, "Cohen")
    assert data["prompt"] == "I found 1 apartment."
    assert "Yosef" not in data["reply"] and "People" not in data["reply"]
    assert [c["action"] for c in data["choices"]] == ["open_apartment"]


@pytest.mark.parametrize("role", ["shliach", "campaign_manager", None])
def test_other_roles_are_never_offered_adding(client, fake_crm, role):
    as_role(fake_crm, role)
    fake_crm.answers.append(found(can_add=True))
    data = turn(client, "Nobody Here")
    assert data["outcome"] == "no_match" and data["choices"] == []
    assert "add" not in data["reply"].lower() and data["search_state"]["offer_add"] is False


@pytest.mark.parametrize("role", ["shliach", "campaign_manager", None])
def test_other_roles_cannot_start_adding_by_any_route(client, fake_crm, ai, role):
    as_role(fake_crm, role)
    ai.understandings.append({"action": "create_contact", "person": "Yosef Cohen"})
    data = turn(client, "add Yosef Cohen")
    assert data["outcome"] == "add_not_allowed" and fake_crm.requests == []
    as_role(fake_crm, role)
    response = client.post("/assistant/turn", headers=AUTH, json={"intent": "create_contact", "message": ""})
    assert response.json()["outcome"] == "add_not_allowed" and fake_crm.requests == []


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_admin_and_staff_see_people(client, fake_crm, role):
    as_role(fake_crm, role)
    fake_crm.answers.append(found(people=[person()]))
    assert turn(client, "Cohen")["prompt"] == "I found 1 person."


# --- no results: the add offer -------------------------------------------------------

def test_no_match_offers_adding_when_the_crm_allows_it(client, fake_crm):
    fake_crm.answers.append(found(can_add=True))
    data = turn(client, "Yosef Cohen")
    assert data["reply"] == ('I found no matching person you can access for "Yosef Cohen". '
                             "Do you want to add a new person?")
    assert [c["action"] for c in data["choices"]] == ["add_person", "dismiss"]
    assert data["search_state"]["offer_add"] is True


def test_yes_to_the_offer_starts_the_existing_add_path_and_saves_nothing(client, fake_crm):
    data = turn(client, "yes", search_state={"offer_add": True, "language": "en"})
    assert data["intent"] == "create_contact" and data["outcome"] == "started"
    assert data["awaiting_confirmation"] is None and fake_crm.requests == []


def test_no_to_the_offer_goes_back_to_the_text_box(client, fake_crm):
    data = turn(client, "לא", language="he", search_state={"offer_add": True, "language": "he"})
    assert data["outcome"] == "add_declined" and HEBREW.search(data["reply"])
    assert data["search_state"]["offer_add"] is False and fake_crm.requests == []


def test_other_text_after_the_offer_is_a_new_search(client, fake_crm):
    fake_crm.answers.append(found(apartments=[apt()]))
    data = turn(client, "Herzl", search_state={"offer_add": True, "language": "en"})
    assert data["outcome"] == "results" and searched(fake_crm) == ["Herzl"]
    assert data["search_state"]["offer_add"] is False


def test_no_match_without_permission_says_so_plainly(client, fake_crm):
    fake_crm.answers.append(found(can_add=False))
    data = turn(client, "Yosef Cohen")
    assert data["reply"] == 'I found no matching person or apartment you can access for "Yosef Cohen".'
    assert data["choices"] == [] and data["search_state"]["offer_add"] is False


@pytest.mark.parametrize("value", [None, "true", 1])
def test_only_an_explicit_true_allows_the_offer(client, fake_crm, value):
    fake_crm.answers.append(ok({"people": [], "apartments": [], "people_truncated": False,
                                "apartments_truncated": False, "can_add_person": value}))
    assert turn(client, "Yosef Cohen")["choices"] == []


def test_the_search_state_refuses_extra_keys(client, fake_crm):
    response = client.post("/assistant/turn", headers=AUTH, json={
        "intent": "search", "message": "yes", "search_state": {"offer_add": True, "results": [1]}})
    assert response.status_code == 422


# --- understood requests (docs/instructions21.md; more in tests/test_interpreter.py) ---------

@pytest.mark.parametrize("words, expected", [
    ("Cohen", ["Cohen"]),
    ("to Yosef Cohen", ["Yosef Cohen"]),
    ("את דנה", ["דנה"]),
    ("לג'ון", ["לג'ון", "ג'ון"]),        # a joined preposition is uncertain: as written first
    ("ל-ג'ון", ["ג'ון"]),                # with a hyphen it is certain
    ("לאה כהן", ["לאה כהן", "אה כהן"]),  # "לאה" is searched as written first
    ("משה לוי", ["משה לוי"]),            # מ, ב, ו are never removed
    ("Herzl 10, apt 3.", ["Herzl 10, apt 3"]),
    ("the", ["the"]),                     # never removed down to nothing
])
def test_search_words_only_lose_words_never_gain_or_change_letters(words, expected):
    assert search_chat.search_texts(words) == expected


def test_a_name_that_starts_like_a_preposition_is_found_as_written(client, fake_crm, ai):
    ai.understandings.append({"action": "search", "person": "לאה כהן"})
    fake_crm.answers.append(found(people=[person(name="לאה", last_name="כהן")]))
    turn(client, "חפש את לאה כהן", language="he")
    assert searched(fake_crm) == ["לאה כהן"]


def test_add_with_a_name_searches_first_and_offers_adding_anyway_with_the_typed_details(client, fake_crm, ai):
    text = "add Yosef Cohen, his phone is 052-1234567"
    ai.understandings.append({"action": "create_contact", "person": "Yosef Cohen"})
    fake_crm.answers.append(found(people=[person()]))
    data = turn(client, text)
    assert searched(fake_crm) == ["Yosef Cohen"]
    assert data["prompt"].startswith('Before adding "Yosef Cohen", here is who already exists:')
    assert data["choices"][-1] == {"label": "Add a new person", "action": "add_person", "message": text}


def test_add_with_a_name_nobody_has_goes_straight_to_the_draft_with_every_detail(client, fake_crm, ai):
    text = "פגשתי היום את משה לוי, גר בהרצל 10, הטלפון שלו 050-1234567"
    ai.understandings.append({"action": "create_contact", "person": "משה לוי"})
    ai.extractions.append({"first_name": "משה", "last_name": "לוי", "phone": "050-1234567", "address": "הרצל 10"})
    fake_crm.answers += [found(can_add=True),
                         proposal({"first_name": "משה", "last_name": "לוי", "phone": "050-1234567", "address": "הרצל 10"})]
    data = turn(client, text, language="he")
    assert searched(fake_crm) == ["משה לוי"]
    assert ai.extraction_requests[0]["input"].endswith(f"<message>\n{text}\n</message>")
    sent = fake_crm.requests[1]
    assert sent.method == "POST" and sent.url.path == "/api/assistant/proposals"
    assert data["intent"] == "create_contact" and data["outcome"] == "proposal"
    assert data["reply"].startswith('אין עדיין מישהו שמתאים ל-"משה לוי"')
    assert "050-1234567" in data["reply"] and "הרצל 10" in data["reply"]
    assert data["awaiting_confirmation"]["phone"] == "050-1234567"  # shown, not saved: a yes is still needed
    assert len(fake_crm.requests) == 2


def test_yes_to_the_offer_carries_the_searched_words_into_the_add_flow(client, fake_crm, ai):
    fake_crm.answers.append(found(can_add=True))
    data = turn(client, "Yosef Cohen")
    assert data["choices"][0] == {"label": "Yes", "action": "add_person", "message": "Yosef Cohen"}
    ai.extractions.append({})  # nothing read: the flow asks, nothing is sent to the CRM
    data = turn(client, "yes", search_state=data["search_state"])
    assert data["intent"] == "create_contact" and ai.extraction_requests
    assert len(fake_crm.requests) == 1


def test_edit_apartment_with_one_match_opens_it_and_saves_nothing(client, fake_crm, ai):
    ai.understandings.append({"action": "edit_apartment", "person": "Herzl 10", "value": "Jewish"})
    fake_crm.answers += [found(apartments=[apt(57797, 35852)]), ok(apt(57797, 35852, fields=ACTIVIST_FIELDS))]
    data = turn(client, "change Herzl 10 to Jewish")
    assert searched(fake_crm) == ["Herzl 10"]
    opened = fake_crm.requests[1]
    assert opened.url.path == "/api/assistant/apartments/getapartment" and opened.url.params["id"] == "35852"
    assert data["intent"] == "edit_apartment" and data["state"]["step"] == "choose_field"
    assert data["reply"].startswith('You asked to change it to "Jewish".')
    assert {r.method for r in fake_crm.requests} == {"GET"}


def test_edit_apartment_with_several_matches_lists_only_apartments(client, fake_crm, ai):
    ai.understandings.append({"action": "edit_apartment", "person": "Herzl"})
    fake_crm.answers.append(found(people=[person()], apartments=[apt(1, 1), apt(2, 2)]))
    data = turn(client, "change something at Herzl")
    assert data["intent"] == "search" and "Choose the record you want to change:" in data["prompt"]
    assert {c["group"] for c in data["choices"]} == {"apartments"} and len(fake_crm.requests) == 1

# --- opening a result ----------------------------------------------------------------

def test_an_apartment_picked_from_the_results_is_opened_by_its_lineage_id(client, fake_crm):
    fake_crm.answers.append(ok(apt(57797, 35852, fields=ACTIVIST_FIELDS)))
    response = client.post("/assistant/turn", headers=AUTH,
                           json={"intent": "edit_apartment", "message": "", "open_id": 35852})
    data = response.json()
    request = fake_crm.requests[0]
    assert request.method == "GET" and request.url.params["id"] == "35852"
    assert data["state"]["step"] == "choose_field" and data["state"]["lineage_id"] == 35852
    assert [c["label"] for c in data["choices"]][:2] == ["1. Jewish status", "2. Phone"]


def test_an_apartment_the_user_may_not_see_is_gone(client, fake_crm):
    fake_crm.answers.append(httpx.Response(404, json={"detail": "Apartment not found"}))
    response = client.post("/assistant/turn", headers=AUTH,
                           json={"intent": "edit_apartment", "message": "", "open_id": 999})
    assert response.json()["outcome"] == "gone"


@pytest.mark.parametrize("open_id", [0, -3, "x"])
def test_open_id_must_be_a_positive_number(client, fake_crm, open_id):
    response = client.post("/assistant/turn", headers=AUTH,
                           json={"intent": "edit_apartment", "message": "", "open_id": open_id})
    assert response.status_code == 422 and fake_crm.requests == []


def test_a_person_picked_from_the_results_is_opened_from_the_crm(client, fake_crm):
    """The person flow itself is tested in tests/test_person_chat.py."""
    fake_crm.answers.append(httpx.Response(404, json={"detail": "Contact not found"}))
    data = client.post("/assistant/turn", headers=AUTH,
                       json={"intent": "person", "message": "", "open_id": 5}).json()
    (request,) = fake_crm.requests
    assert request.method == "GET" and request.url.path == "/api/assistant/contacts/5"
    assert data["intent"] == "person" and data["outcome"] == "gone"


@pytest.mark.parametrize("role", ["shliach", "campaign_manager", None, "superadmin"])
def test_a_person_is_never_opened_for_other_roles(client, fake_crm, monkeypatch, role):
    monkeypatch.setenv("CRM_WEB_URL", "https://app.example.org")
    as_role(fake_crm, role)
    data = client.post("/assistant/turn", headers=AUTH,
                       json={"intent": "person", "message": "", "open_id": 5}).json()
    assert data["outcome"] == "forbidden" and "link" not in data
    assert fake_crm.requests == []  # refused before any contact call


# --- CRM failures ----------------------------------------------------------------------

@pytest.mark.parametrize("answer, outcome", [
    (httpx.Response(401, json={"detail": "Could not validate credentials"}), "unauthorized"),
    (httpx.Response(403, json={"detail": "Not allowed"}), "forbidden"),
    (httpx.Response(404, json={"detail": "Not Found"}), "search_missing"),
    (httpx.Response(500, json={}), "crm_failed"),
    (httpx.Response(302, headers={"location": "http://elsewhere/"}), "crm_failed"),
    (httpx.ReadTimeout("slow"), "crm_timeout"),
    (httpx.ConnectError("refused"), "crm_unreachable"),
    (ok({"people": "x", "apartments": []}), "crm_failed"),
    (ok({"people": [{"id": "5"}], "apartments": []}), "crm_failed"),
    (ok({"people": [], "apartments": [{"apartment_id": 1}]}), "crm_failed"),
])
def test_crm_failures(client, fake_crm, answer, outcome):
    fake_crm.answers.append(answer)
    data = turn(client, "Cohen")
    assert data["outcome"] == outcome
    assert data["logout"] is (outcome == "unauthorized")
    assert len(fake_crm.requests) == 1  # never retried by itself


def test_a_missing_crm_address_is_reported(client, fake_crm, monkeypatch):
    monkeypatch.delenv("CRM_API_URL")
    response = client.post("/assistant/turn", headers=AUTH, json={"intent": "search", "message": "Cohen"})
    assert response.status_code == 503  # the login check itself can't run


def test_a_search_needs_a_login(client, fake_crm):
    response = client.post("/assistant/turn", json={"intent": "search", "message": "Cohen"})
    assert response.status_code == 401 and fake_crm.requests == []


# --- language ----------------------------------------------------------------------------

def test_hebrew_replies_have_no_english_sentences(client, fake_crm):
    english = re.compile(r"[A-Za-z]{2,}(?:[ ,'-]+[A-Za-z]{2,}){2,}")
    for key, text in search_chat.MESSAGES["he"].items():
        if isinstance(text, str) and key not in ("person_line",):
            assert not english.search(text.replace("CRM_API_URL", "")), key
