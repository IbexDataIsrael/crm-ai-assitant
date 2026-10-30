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

SEARCH_PATH = "/api/assistant/search"
HEBREW = re.compile(r"[֐-׿]")


@pytest.fixture
def client():
    return TestClient(app)


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
    assert reply[5] == "- Herzl 10, Tel Aviv, apartment 3, floor 1 - Cohen (#57797)"
    assert [(c["action"], c["id"], c["group"]) for c in data["choices"]] == [
        ("open_person", 5, "people"), ("open_person", 6, "people"), ("open_apartment", 35852, "apartments")]
    assert data["search_state"] == {"offer_add": False, "language": "en"}


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
def test_other_roles_cannot_start_adding_by_any_route(client, fake_crm, role):
    as_role(fake_crm, role)
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


# --- short request forms ---------------------------------------------------------------

@pytest.mark.parametrize("message, expected", [
    ("Cohen", ("search", "Cohen", None)),
    ("Herzl 10 Tel Aviv", ("search", "Herzl 10 Tel Aviv", None)),
    ("find Dana", ("search", "Dana", None)),
    ("please search Herzl 10", ("search", "Herzl 10", None)),
    ("חפש את דנה", ("search", "את דנה", None)),
    ("add Yosef Cohen", ("add", "Yosef Cohen", None)),
    ("add a new contact: Yosef Cohen", ("add", "Yosef Cohen", None)),
    ("Please add contact Yosef Cohen", ("add", "Yosef Cohen", None)),
    ("הוסף יוסף כהן", ("add", "יוסף כהן", None)),
    ("תוסיף איש קשר חדש יוסף כהן", ("add", "יוסף כהן", None)),
    ("צור איש קשר", ("add", "", None)),
    ("add", ("add", "", None)),
    ("change 2C to English", ("change", "2C", "English")),
    ("change Herzl 10 apartment 3 to Jewish", ("change", "Herzl 10 apartment 3", "Jewish")),
    ("שנה את 2C לאנגלית", ("change", "את 2C", "אנגלית")),
    ("עדכן הרצל 10 ל-יהודי", ("change", "הרצל 10", "יהודי")),
    ("change 2C", ("search", "2C", None)),
    ("how do I add a contact?", ("search", "how do I add a contact?", None)),
    ("Addison Road 5", ("search", "Addison Road 5", None)),
])
def test_parse(message, expected):
    assert search_chat.parse(message) == expected


def test_add_with_a_name_searches_first_and_offers_adding_anyway(client, fake_crm):
    fake_crm.answers.append(found(people=[person()]))
    data = turn(client, "add Yosef Cohen")
    assert searched(fake_crm) == ["Yosef Cohen"]
    assert data["prompt"].startswith('Before adding "Yosef Cohen", here is who already exists:')
    assert data["choices"][-1] == {"label": "Add a new person", "action": "add_person"}


def test_add_with_a_name_nobody_has_starts_adding(client, fake_crm):
    fake_crm.answers.append(found(can_add=True))
    data = turn(client, "הוסף איש קשר יוסף כהן", language="he")
    assert searched(fake_crm) == ["יוסף כהן"]
    assert data["intent"] == "create_contact" and data["reply"].startswith('אין עדיין מישהו שמתאים ל-"יוסף כהן"')
    assert len(fake_crm.requests) == 1  # nothing proposed or saved yet


def test_add_without_a_name_starts_adding_without_a_search(client, fake_crm):
    data = turn(client, "add a contact")
    assert data["intent"] == "create_contact" and fake_crm.requests == []


def test_change_with_one_apartment_opens_it_and_saves_nothing(client, fake_crm):
    fake_crm.answers += [found(apartments=[apt(57797, 35852)]), ok(apt(57797, 35852, fields=ACTIVIST_FIELDS))]
    data = turn(client, "change Herzl 10 to Jewish")
    assert searched(fake_crm) == ["Herzl 10"]
    opened = fake_crm.requests[1]
    assert opened.url.path == "/api/assistant/apartments/getapartment" and opened.url.params["id"] == "35852"
    assert data["intent"] == "edit_apartment" and data["state"]["step"] == "choose_field"
    assert data["reply"].startswith('You asked to change it to "Jewish".')
    assert {r.method for r in fake_crm.requests} == {"GET"}


def test_change_with_several_matches_lists_them(client, fake_crm):
    fake_crm.answers.append(found(apartments=[apt(1, 1), apt(2, 2)]))
    data = turn(client, "change Herzl to Jewish")
    assert data["intent"] == "search" and "Choose the record you want to change:" in data["prompt"]
    assert len(fake_crm.requests) == 1


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


def test_a_person_opens_as_a_link_to_the_crm_page(client, fake_crm, monkeypatch):
    monkeypatch.setenv("CRM_WEB_URL", "https://app.example.org/")
    response = client.post("/assistant/turn", headers=AUTH,
                           json={"intent": "open_person", "message": "", "open_id": 5})
    data = response.json()
    assert data["outcome"] == "not_available"
    assert data["link"]["url"] == "https://app.example.org/dashboard/contacts/5"
    assert fake_crm.requests == []  # no contact endpoint is called (none exists yet)


@pytest.mark.parametrize("value", ["", "ftp://x", "https://user:pw@x.org", "https://x.org/?a=1", "not a url"])
def test_no_link_without_a_usable_crm_web_url(client, fake_crm, monkeypatch, value):
    monkeypatch.setenv("CRM_WEB_URL", value)
    data = client.post("/assistant/turn", headers=AUTH,
                       json={"intent": "open_person", "message": "", "open_id": 5}).json()
    assert "link" not in data and "CRM_WEB_URL" in data["reply"]


@pytest.mark.parametrize("role", ["shliach", "campaign_manager", None])
def test_a_person_is_never_opened_for_other_roles(client, fake_crm, monkeypatch, role):
    monkeypatch.setenv("CRM_WEB_URL", "https://app.example.org")
    as_role(fake_crm, role)
    data = client.post("/assistant/turn", headers=AUTH,
                       json={"intent": "open_person", "message": "", "open_id": 5}).json()
    assert "link" not in data and "/dashboard/contacts" not in data["reply"]


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
            assert not english.search(text.replace("CRM_WEB_URL", "").replace("CRM_API_URL", "")), key
