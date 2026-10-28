"""The edit-an-apartment conversation (POST /assistant/turn, intent edit_apartment).

The CRM is a mock transport (fake_crm); nothing reaches a real CRM or AI
provider (no model is used in this flow at all). CRM answers copy
sheepsidebay_crm backend/app/routers/assistant_apartments.py (fixes114).
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

TOKEN = "eyJhbGciOiJIUzI1NiJ9.user-a-secret.sig"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

FIELDS = {"fields": [
    {"field": "jewish_status", "field_key": "apartment.Jewish_status", "type": "single",
     "label_he": "סטטוס יהודי", "label_en": "Jewish status",
     "options": [{"value": "jewish", "label_he": "יהודי", "label_en": "Jewish"},
                 {"value": "not_jewish", "label_he": "לא יהודי", "label_en": "Not Jewish"},
                 {"value": "unknown", "label_he": "לא ידוע", "label_en": "Unknown"}]},
    {"field": "language", "field_key": "apartment.language", "type": "multi",
     "label_he": "שפה", "label_en": "Language",
     "options": [{"value": "hebrew", "label_he": "עברית", "label_en": "Hebrew"},
                 {"value": "russian", "label_he": "רוסית", "label_en": "Russian"},
                 {"value": "english", "label_he": "אנגלית", "label_en": "English"}]},
]}


def apt(apartment_id=11, lineage_id=11, **values):
    return {"apartment_id": apartment_id, "lineage_id": lineage_id, "building_address": "Herzl 10, Tel Aviv",
            "apartment_name": "3", "floor": "1", "resident_name": "Cohen", "jewish_status": "unknown",
            "language": [], **values}


def ok(body):
    return httpx.Response(200, json=body)


def search(*apartments, truncated=False):
    return ok({"results": list(apartments), "truncated": truncated})


def updated(apartment, previous=11, status="updated"):
    return ok({"status": status, "apartment_id": apartment["apartment_id"],
               "previous_apartment_id": previous if status == "updated" else None,
               "lineage_id": apartment["lineage_id"], "apartment": apartment})


@pytest.fixture
def client():
    return TestClient(app)


def turn(client, message="", state=None, language=None):
    body = {"intent": "edit_apartment", "message": message}
    if state is not None:
        body["apartment_state"] = state
    if language:
        body["language"] = language
    response = client.post("/assistant/turn", json=body, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def posts(fake_crm):
    return [r for r in fake_crm.requests if r.method == "POST"]


def at_value(client, fake_crm, field_choice="1", apartment=None):
    """Walk to the value step: address -> one match -> field."""
    fake_crm.answers += [search(apartment or apt()), ok(FIELDS)]
    data = turn(client, "Herzl 10")
    return turn(client, field_choice, data["state"])


# --- the order the owner asked for ---------------------------------------------

def test_menu_choice_asks_for_the_address_and_calls_nothing(client, fake_crm):
    data = turn(client)
    assert data["intent"] == "edit_apartment" and data["outcome"] == "started"
    assert "address" in data["reply"]
    assert fake_crm.requests == []


def test_address_goes_to_the_crm_which_returns_full_address_id_and_fields(client, fake_crm):
    fake_crm.answers += [search(apt()), ok(FIELDS)]
    data = turn(client, "Herzl 10")
    first, second = fake_crm.requests
    assert first.method == "GET" and first.url.path == "/api/assistant/apartments/search"
    assert first.url.params["q"] == "Herzl 10"
    assert second.url.path == "/api/assistant/apartments/fields"
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in fake_crm.requests)
    assert "Apartment #11: Herzl 10, Tel Aviv, apartment 3, floor 1" in data["reply"]
    assert "- Jewish status: Unknown" in data["reply"]
    # The field list is the CRM's, numbered, with buttons.
    assert "1. Jewish status\n2. Language" in data["reply"]
    assert [c["message"] for c in data["choices"]] == ["1", "2"]
    assert data["state"]["step"] == "choose_field" and data["outcome"] == "choose_field"


def test_several_matches_are_listed_and_picked_by_number(client, fake_crm):
    fake_crm.answers += [search(apt(), apt(12, 12, apartment_name="4", resident_name="Levi"), truncated=True)]
    data = turn(client, "Herzl")
    assert data["outcome"] == "choose_apartment"
    assert "2. Herzl 10, Tel Aviv, apartment 4, floor 1 - Levi (#12)" in data["reply"]
    assert "Only the first 2 are shown" in data["reply"]
    picked = turn(client, "9", data["state"])  # not on the list: asked again, no CRM call
    assert "choose one of the numbers" in picked["reply"] and len(fake_crm.requests) == 1
    fake_crm.answers.append(ok(FIELDS))
    picked = turn(client, "2", data["state"])
    assert "Apartment #12" in picked["reply"] and picked["state"]["apartment"]["apartment_id"] == 12


def test_no_match_asks_again(client, fake_crm):
    fake_crm.answers.append(search())
    data = turn(client, "Nowhere 1")
    assert data["outcome"] == "no_match" and data["state"]["step"] == "address"


def test_single_value_flow_sends_id_field_value_and_shows_what_the_crm_saved(client, fake_crm):
    data = at_value(client, fake_crm)
    assert data["outcome"] == "choose_value" and data["multi"] is False
    assert "current value: Unknown" in data["reply"] and "2. Not Jewish" in data["reply"]
    # The CRM answers with ITS state - here deliberately not what was sent.
    fake_crm.answers.append(updated(apt(21, 11, jewish_status="jewish")))
    saved = turn(client, "2", data["state"])
    post = posts(fake_crm)[-1]
    assert post.url.path == "/api/assistant/apartments/11/update"
    assert json.loads(post.content) == {"field": "jewish_status", "value": "not_jewish"}
    assert saved["reply"].startswith("Saved. The CRM now shows:\nApartment #21:")
    assert "- Jewish status: Jewish" in saved["reply"] and "Not Jewish" not in saved["reply"].split("Choose")[0]
    # The next edit uses the NEW id (an edit changes the apartment's id).
    assert saved["state"]["apartment"]["apartment_id"] == 21 and saved["state"]["step"] == "choose_field"
    nxt = turn(client, "1", saved["state"])
    fake_crm.answers.append(updated(apt(22, 11, jewish_status="unknown"), previous=21))
    turn(client, "Unknown", nxt["state"])  # an option's label works too
    assert posts(fake_crm)[-1].url.path == "/api/assistant/apartments/21/update"


def test_multi_value_field_takes_several_numbers_or_none(client, fake_crm):
    data = at_value(client, fake_crm, "2")
    assert data["multi"] is True and "separated by commas" in data["reply"]
    fake_crm.answers.append(updated(apt(21, 11, language=["hebrew", "english"])))
    saved = turn(client, "1, 3", data["state"])
    assert json.loads(posts(fake_crm)[-1].content) == {"field": "language", "value": ["hebrew", "english"]}
    assert "- Language: Hebrew, English" in saved["reply"]
    again = turn(client, "2", saved["state"])
    fake_crm.answers.append(updated(apt(22, 11, language=[]), previous=21))
    cleared = turn(client, "none", again["state"])
    assert json.loads(posts(fake_crm)[-1].content)["value"] == []
    assert "- Language: -" in cleared["reply"]


def test_unchanged_is_reported_without_a_new_version(client, fake_crm):
    data = at_value(client, fake_crm)
    fake_crm.answers.append(updated(apt(), status="unchanged"))
    answer = turn(client, "3", data["state"])
    assert answer["outcome"] == "choose_field" and answer["reply"].startswith("Nothing to change")
    assert answer["state"]["apartment"]["apartment_id"] == 11


def test_a_value_not_on_the_list_is_asked_again_without_a_crm_call(client, fake_crm):
    data = at_value(client, fake_crm)
    before = len(fake_crm.requests)
    again = turn(client, "maybe", data["state"])
    assert "choose one of the numbers" in again["reply"] and len(fake_crm.requests) == before


def test_no_editable_fields_ends_the_conversation(client, fake_crm):
    fake_crm.answers += [search(apt()), ok({"fields": []})]
    data = turn(client, "Herzl 10")
    assert data["outcome"] == "no_fields" and data["show_menu"] is True


# --- conflicts and lost answers --------------------------------------------------

def test_409_rereads_by_lineage_and_never_resends(client, fake_crm):
    data = at_value(client, fake_crm, apartment=apt(15, 11))
    fake_crm.answers += [httpx.Response(409, json={"detail": "changed"}), ok(apt(30, 11, jewish_status="jewish"))]
    answer = turn(client, "2", data["state"])
    assert len(posts(fake_crm)) == 1
    assert fake_crm.requests[-1].method == "GET"
    assert fake_crm.requests[-1].url.path == "/api/assistant/apartments/by-lineage/11"
    assert "changed by someone else" in answer["reply"] and "Apartment #30" in answer["reply"]
    assert answer["state"]["step"] == "choose_value" and answer["state"]["apartment"]["apartment_id"] == 30


def test_404_with_a_gone_lineage_ends(client, fake_crm):
    data = at_value(client, fake_crm)
    fake_crm.answers += [httpx.Response(404, json={"detail": "Apartment not found"}),
                         httpx.Response(404, json={"detail": "Apartment not found"})]
    answer = turn(client, "2", data["state"])
    assert answer["outcome"] == "gone" and answer["show_menu"] is True and len(posts(fake_crm)) == 1


@pytest.mark.parametrize("lost", [
    httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("dropped"),
    httpx.Response(502, text="bad gateway"), httpx.Response(200, text="not json"),
])
def test_a_lost_answer_blocks_the_edit_and_yes_only_rereads(client, fake_crm, lost):
    data = at_value(client, fake_crm)
    fake_crm.answers.append(lost)
    blocked = turn(client, "2", data["state"])
    assert blocked["outcome"] == "uncertain" and blocked["awaiting_check"] is True
    assert "won't send it again" in blocked["reply"]
    # Anything but yes / no: still blocked, nothing sent.
    before = len(fake_crm.requests)
    wait = turn(client, "1", blocked["state"])
    assert wait["outcome"] == "blocked" and len(fake_crm.requests) == before
    # yes -> a read; the CRM does not show the change.
    fake_crm.answers.append(ok(apt(11, 11, jewish_status="unknown")))
    check = turn(client, "yes", blocked["state"])
    assert check["outcome"] == "not_confirmed" and check["state"]["step"] == "blocked"
    assert "(Jewish status: Not Jewish) does not show" in check["reply"]
    # yes again -> the CRM shows it now.
    fake_crm.answers.append(ok(apt(40, 11, jewish_status="not_jewish")))
    check = turn(client, "yes", check["state"])
    assert check["reply"].startswith("Checked: the CRM shows the change was saved")
    assert check["state"]["step"] == "choose_field" and check["state"]["apartment"]["apartment_id"] == 40
    assert len(posts(fake_crm)) == 1  # the update was sent exactly once


def test_no_while_blocked_stops(client, fake_crm):
    data = at_value(client, fake_crm)
    fake_crm.answers.append(httpx.ReadTimeout("slow"))
    blocked = turn(client, "2", data["state"])
    stop = turn(client, "no", blocked["state"])
    assert stop["outcome"] == "stopped" and stop["show_menu"] is True and len(posts(fake_crm)) == 1


def test_no_connection_means_not_sent(client, fake_crm):
    data = at_value(client, fake_crm)
    fake_crm.answers.append(httpx.ConnectError("refused"))
    answer = turn(client, "2", data["state"])
    assert answer["reply"].startswith("I couldn't reach the CRM, so the change was not sent")
    assert answer["state"]["step"] == "choose_value"


# --- refusals --------------------------------------------------------------------

def test_401_logs_out(client, fake_crm):
    data = at_value(client, fake_crm)
    fake_crm.answers.append(httpx.Response(401, json={"detail": "Could not validate credentials"}))
    answer = turn(client, "2", data["state"])
    assert answer["logout"] is True and answer["state"]["apartment"] is None


def test_403_reloads_the_fields_the_admin_allows(client, fake_crm):
    data = at_value(client, fake_crm)
    only_language = {"fields": [FIELDS["fields"][1]]}
    fake_crm.answers += [httpx.Response(403, json={"detail": "Your organization does not let activists edit this field"}),
                         ok(only_language)]
    answer = turn(client, "2", data["state"])
    assert "did not allow this change (Your organization does not let activists edit this field)" in answer["reply"]
    assert answer["state"]["step"] == "choose_field" and [c["label"] for c in answer["choices"]] == ["Language"]


def test_422_reloads_options_and_asks_again(client, fake_crm):
    data = at_value(client, fake_crm)
    fake_crm.answers += [httpx.Response(422, json={"detail": {"status": "invalid", "invalid_fields": [
        {"field": "jewish_status", "code": "value_not_allowed", "value": "not_jewish"}]}}), ok(FIELDS)]
    answer = turn(client, "2", data["state"])
    assert "did not accept that value for Jewish status (value_not_allowed)" in answer["reply"]
    assert answer["state"]["step"] == "choose_value"


def test_a_bad_crm_search_shape_is_not_trusted(client, fake_crm):
    fake_crm.answers.append(ok({"results": [{"apartment_id": "11"}]}))
    data = turn(client, "Herzl")
    assert data["outcome"] == "crm_failed" and "crm_contract_error" in data["reply"]


# --- language, state, token -------------------------------------------------------

def test_hebrew_flow(client, fake_crm):
    fake_crm.answers += [search(apt(building_address="הרצל 10")), ok(FIELDS)]
    data = turn(client, "הרצל 10")
    assert "דירה #11: הרצל 10, דירה 3, קומה 1" in data["reply"] and "1. סטטוס יהודי" in data["reply"]
    value = turn(client, "1", data["state"])
    assert "הערך הנוכחי: לא ידוע" in value["reply"]
    fake_crm.answers.append(updated(apt(21, 11, building_address="הרצל 10", jewish_status="jewish")))
    saved = turn(client, "יהודי", value["state"])
    assert saved["reply"].startswith("נשמר.") and "- סטטוס יהודי: יהודי" in saved["reply"]


def test_state_with_extra_keys_is_refused(client, fake_crm):
    body = {"intent": "edit_apartment", "message": "1",
            "apartment_state": {"step": "choose_field", "organization_id": 2}}
    assert client.post("/assistant/turn", json=body, headers=AUTH).status_code == 422


def test_the_token_never_appears_in_replies_or_state(client, fake_crm):
    data = at_value(client, fake_crm)
    fake_crm.answers.append(updated(apt(21, 11, jewish_status="jewish")))
    saved = turn(client, "1", data["state"])
    assert TOKEN not in json.dumps(data) and TOKEN not in json.dumps(saved)
