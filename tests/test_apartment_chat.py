"""The edit-an-apartment conversation (POST /assistant/turn, intent edit_apartment).

The CRM is a mock transport (fake_crm); nothing reaches a real CRM or AI
provider (no model is used in this flow at all). CRM answers copy the shapes
of sheepsidebay_crm backend/app/routers/assistant_apartments.py and
backend/app/apartment_edit_rules.py (fixes115): every apartment carries its
own `editable_fields`, decided by the CRM per user and apartment.
"""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

TOKEN = "eyJhbGciOiJIUzI1NiJ9.user-a-secret.sig"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ROOT = Path(__file__).resolve().parent.parent

# Field entries as the CRM's apartment_views builds them.
STATUS = {"field_key": "apartment.Jewish_status", "label": "סטטוס יהודי", "label_en": "Jewish status",
          "kind": "single_option", "current_value": "not_known",
          "options": [{"value": "jewish", "label": "יהודי", "label_en": "Jewish"},
                      {"value": "not_jewish", "label": "לא יהודי", "label_en": "Not Jewish"},
                      {"value": "not_known", "label": "לא ידוע", "label_en": "Unknown"}]}
LANGUAGES = {"field_key": "apartment.language", "label": "שפה", "label_en": "Language",
             "kind": "multi_option", "current_value": [],
             "options": [{"value": "hebrew", "label": "עברית", "label_en": "Hebrew"},
                         {"value": "russian", "label": "רוסית", "label_en": "Russian"},
                         {"value": "english", "label": "אנגלית", "label_en": "English"}]}
PHONE = {"field_key": "apartments.phone", "label": "טלפון", "label_en": "Phone", "kind": "text",
         "current_value": "0521234567"}
SIZE = {"field_key": "customfield.apartment.family_size", "label": "גודל משפחה", "label_en": "Family size",
        "kind": "number", "current_value": 4.0}
VISIT = {"field_key": "customfield.apartment.last_visit", "label": "ביקור אחרון", "label_en": "Last visit",
         "kind": "date", "current_value": "2026-01-15"}
MEZUZAH = {"field_key": "customfield.apartment.has_mezuzah", "label": "יש מזוזה", "label_en": "Has mezuzah",
           "kind": "yes_no", "current_value": None}
REPORT = {"field_key": "apartments.report_number_id", "label": "מספר דוח", "label_en": "Report number",
          "kind": "single_option", "current_value": 3,
          "options": [{"value": n, "label": f"R-{n:02d}", "label_en": f"R-{n:02d}"} for n in range(1, 26)]}
ADMIN_FIELDS = [STATUS, LANGUAGES, PHONE, SIZE, VISIT, MEZUZAH, REPORT]
ACTIVIST_FIELDS = [STATUS, PHONE]


def apt(apartment_id=11, lineage_id=11, fields=None, **values):
    return {"apartment_id": apartment_id, "lineage_id": lineage_id, "building_address": "Herzl 10, Tel Aviv",
            "apartment_name": "3", "floor": "1", "resident_name": "Cohen",
            "editable_fields": [dict(f) for f in (ADMIN_FIELDS if fields is None else fields)], **values}


def with_value(apartment, field_key, value):
    for entry in apartment["editable_fields"]:
        if entry["field_key"] == field_key:
            entry["current_value"] = value
    return apartment


def ok(body):
    return httpx.Response(200, json=body)


def search(*apartments, truncated=False):
    return ok({"results": list(apartments), "truncated": truncated})


def answer(apartment, field_key, status="updated", previous=11, value="missing"):
    """The CRM's update answer; `value` defaults to the field's current value in `apartment`."""
    if value == "missing":
        value = next((f["current_value"] for f in apartment["editable_fields"] if f["field_key"] == field_key), None)
    return ok({"status": status, "field_key": field_key, "value": value,
               "apartment_id": apartment["apartment_id"],
               "previous_apartment_id": previous if status == "updated" else None,
               "lineage_id": apartment["lineage_id"], "apartment": apartment})


def invalid(field_key, code):
    return httpx.Response(422, json={"detail": {"status": "invalid", "invalid_fields": [
        {"field": field_key, "code": code}]}})


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


def number_of(field):
    return str(ADMIN_FIELDS.index(field) + 1)


def at_value(client, fake_crm, field=STATUS, apartment=None):
    """address -> one match -> field (a fresh read) -> the value question."""
    apartment = apartment or apt()
    fake_crm.answers += [search(apartment), ok(apartment)]
    data = turn(client, "Herzl 10")
    return turn(client, number_of(field), data["state"])


def at_confirm(client, fake_crm, field=STATUS, value="2", apartment=None):
    apartment = apartment or apt()
    data = at_value(client, fake_crm, field, apartment)
    fake_crm.answers.append(ok(apartment))
    confirm = turn(client, value, data["state"])
    assert confirm["outcome"] == "confirm", confirm["reply"]
    return confirm


# --- the order the owner asked for ---------------------------------------------

def test_menu_choice_asks_for_the_address_and_calls_nothing(client, fake_crm):
    data = turn(client)
    assert data["intent"] == "edit_apartment" and data["outcome"] == "started"
    assert "address" in data["reply"]
    assert fake_crm.requests == []


def test_one_match_shows_the_apartment_and_the_crms_own_field_list(client, fake_crm):
    fake_crm.answers.append(search(apt()))
    data = turn(client, "Herzl 10")
    (request,) = fake_crm.requests  # no separate field-list call any more
    assert request.method == "GET" and request.url.path == "/api/assistant/apartments/search"
    assert request.url.params["q"] == "Herzl 10"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert "Apartment #11: Herzl 10, Tel Aviv, apartment 3, floor 1 - Cohen" in data["reply"]
    for line in ("- Jewish status: Unknown", "- Language: -", "- Phone: 0521234567", "- Family size: 4",
                 "- Last visit: 2026-01-15", "- Has mezuzah: -", "- Report number: R-03"):
        assert line in data["reply"]
    labels = ["Jewish status", "Language", "Phone", "Family size", "Last visit", "Has mezuzah", "Report number"]
    assert "\n".join(f"{i}. {label}" for i, label in enumerate(labels, 1)) in data["reply"]
    assert [c["message"] for c in data["choices"]] == [str(i) for i in range(1, 8)]
    # The page shows the list once, as buttons: the prompt has no list.
    assert "1. Jewish status" not in data["prompt"]
    assert data["state"]["step"] == "choose_field" and data["outcome"] == "choose_field"


def test_no_match_asks_again(client, fake_crm):
    fake_crm.answers.append(search())
    data = turn(client, "Nowhere 1")
    assert data["outcome"] == "no_match" and data["state"]["step"] == "address"
    assert "Nowhere 1" in data["reply"]


def test_many_matches_are_numbered_with_the_truncated_note_and_picked_by_number(client, fake_crm):
    second = apt(12, 12, apartment_name="4", resident_name="Levi")
    fake_crm.answers.append(search(apt(), second, truncated=True))
    data = turn(client, "Herzl")
    assert data["outcome"] == "choose_apartment"
    assert "2. Herzl 10, Tel Aviv, apartment 4, floor 1 - Levi (#12)" in data["reply"]
    assert "Only the first 2 are shown" in data["reply"]
    assert len(data["choices"]) == 2
    for wrong in ("9", "0", "abc"):  # not on the list: asked again, no CRM call
        again = turn(client, wrong, data["state"])
        assert "choose one of the numbers" in again["reply"] and len(fake_crm.requests) == 1
    fake_crm.answers.append(ok(second))
    picked = turn(client, "2", data["state"])
    assert fake_crm.requests[-1].url.path == "/api/assistant/apartments/getapartment"
    assert fake_crm.requests[-1].url.params["id"] == "12"
    assert "Apartment #12" in picked["reply"] and picked["state"]["apartment_id"] == 12


def test_an_activist_gets_exactly_the_fewer_fields_the_crm_returns(client, fake_crm):
    fake_crm.answers.append(search(apt(fields=ACTIVIST_FIELDS)))
    activist = turn(client, "Herzl 10")
    fake_crm.answers.append(search(apt()))
    admin = turn(client, "Herzl 10")
    assert [c["label"] for c in activist["choices"]] == ["1. Jewish status", "2. Phone"]
    assert len(admin["choices"]) == 7
    assert "Language" not in activist["reply"]


def test_an_empty_field_list_says_nothing_can_be_edited(client, fake_crm):
    fake_crm.answers.append(search(apt(fields=[])))
    data = turn(client, "Herzl 10")
    assert data["outcome"] == "no_fields" and data["show_menu"] is True
    assert "Nothing in this apartment can be edited by you here" in data["reply"]
    assert posts(fake_crm) == []


def test_a_kind_the_chat_does_not_know_is_left_out(client, fake_crm):
    odd = {"field_key": "customfield.apartment.geo", "label": "מיקום", "label_en": "Location", "kind": "map",
           "current_value": None}
    fake_crm.answers.append(search(apt(fields=[odd, PHONE])))
    data = turn(client, "Herzl 10")
    assert [c["label"] for c in data["choices"]] == ["1. Phone"]


# --- each kind -------------------------------------------------------------------

def test_single_option_confirm_then_send_then_show_what_the_crm_returned(client, fake_crm):
    value = at_value(client, fake_crm)
    assert value["outcome"] == "choose_value" and value["multi"] is False
    assert "current value: Unknown" in value["reply"] and "2. Not Jewish" in value["reply"]
    fake_crm.answers.append(ok(apt()))
    confirm = turn(client, "2", value["state"])
    assert posts(fake_crm) == []  # nothing is sent before the yes
    assert "Change Jewish status of apartment #11:\nfrom: Unknown\nto: Not Jewish" in confirm["reply"]
    assert [c["message"] for c in confirm["choices"]] == ["yes", "no"]
    # The CRM answers with ITS state - here deliberately not what was sent.
    fake_crm.answers.append(answer(with_value(apt(21, 11), STATUS["field_key"], "jewish"), STATUS["field_key"]))
    saved = turn(client, "yes", confirm["state"])
    (post,) = posts(fake_crm)
    assert post.url.path == "/api/assistant/apartments/11/update"
    assert json.loads(post.content) == {"field_key": "apartment.Jewish_status", "value": "not_jewish"}
    assert saved["outcome"] == "updated"
    assert saved["reply"].startswith("Saved. The CRM now shows Jewish status: Jewish.\nApartment #21:")
    assert "Not Jewish" not in saved["reply"].split("Choose another")[0]
    # The next edit uses the NEW id (an edit changes the apartment's id); lineage stays.
    assert saved["state"]["apartment_id"] == 21 and saved["state"]["lineage_id"] == 11
    after = with_value(apt(21, 11), STATUS["field_key"], "jewish")
    fake_crm.answers += [ok(after), ok(after)]
    value = turn(client, "1", saved["state"])
    confirm = turn(client, "Unknown", value["state"])  # an option's label works too
    fake_crm.answers.append(answer(with_value(apt(22, 11), STATUS["field_key"], "not_known"), STATUS["field_key"],
                                   previous=21))
    turn(client, "yes", confirm["state"])
    assert posts(fake_crm)[-1].url.path == "/api/assistant/apartments/21/update"


def test_multi_option_takes_several_numbers_or_none(client, fake_crm):
    data = at_value(client, fake_crm, LANGUAGES)
    assert data["multi"] is True and "separated by commas" in data["reply"]
    fake_crm.answers.append(ok(apt()))
    confirm = turn(client, "1, 3", data["state"])
    assert "to: Hebrew, English" in confirm["reply"]
    fake_crm.answers.append(answer(with_value(apt(21, 11), LANGUAGES["field_key"], ["hebrew", "english"]),
                                   LANGUAGES["field_key"]))
    saved = turn(client, "yes", confirm["state"])
    assert json.loads(posts(fake_crm)[-1].content) == {"field_key": "apartment.language",
                                                        "value": ["hebrew", "english"]}
    assert "Language: Hebrew, English" in saved["reply"]
    fake_crm.answers += [ok(apt(21, 11)), ok(apt(21, 11))]
    value = turn(client, "2", saved["state"])
    assert value["clear"] == {"label": "Clear the value", "message": "[clear]"} and value["multi"]
    confirm = turn(client, value["clear"]["message"], value["state"])
    fake_crm.answers.append(answer(apt(22, 11), LANGUAGES["field_key"], previous=21))
    turn(client, "yes", confirm["state"])
    assert json.loads(posts(fake_crm)[-1].content)["value"] == []


@pytest.mark.parametrize("field, typed, sent, shown", [
    (PHONE, "052-765 4321", "052-765 4321", "052-765 4321"),
    (PHONE, "[clear]", None, "-"),
    (PHONE, "none", "none", "none"),  # a typed word never clears: only the button does
    (SIZE, "12.5", "12.5", "12.5"),
    (SIZE, "[clear]", None, "-"),
    (SIZE, "nothing", "nothing", "nothing"),  # sent as typed; the CRM refuses it as a number
    (VISIT, "2026-10-29", "2026-10-29", "2026-10-29"),
    (VISIT, "29/10/2026", "2026-10-29", "2026-10-29"),  # day first
    (VISIT, "3.4.2026", "2026-04-03", "2026-04-03"),
    (VISIT, "03-04-2026", "2026-04-03", "2026-04-03"),
    (VISIT, "next week", "next week", "next week"),  # not a date form: the CRM checks it
    (VISIT, "[clear]", None, "-"),
])
def test_typed_kinds_are_sent_as_typed_and_only_the_button_clears(client, fake_crm, field, typed, sent, shown):
    value = at_value(client, fake_crm, field)
    assert value["outcome"] == "choose_value"
    assert value["choices"][-1] == value["clear"] == {"label": "Clear the value", "message": "[clear]"}
    fake_crm.answers.append(ok(apt()))
    confirm = turn(client, typed, value["state"])
    assert f"to: {shown}" in confirm["reply"]
    fake_crm.answers.append(answer(apt(21, 11), field["field_key"], value=sent))
    turn(client, "yes", confirm["state"])
    assert json.loads(posts(fake_crm)[-1].content) == {"field_key": field["field_key"], "value": sent}


def test_date_question_names_the_format(client, fake_crm):
    value = at_value(client, fake_crm, VISIT)
    assert "29/10/2026" in value["reply"] and "current value: 2026-01-15" in value["reply"]
    assert "none" not in value["reply"]


@pytest.mark.parametrize("typed", ["31/02/2026", "29/13/2026", "10/29/2026"])
def test_an_impossible_day_first_date_is_asked_again_without_an_update(client, fake_crm, typed):
    value = at_value(client, fake_crm, VISIT)
    fake_crm.answers.append(ok(apt()))
    again = turn(client, typed, value["state"])
    assert again["outcome"] == "choose_value" and "not a real date" in again["reply"]
    assert posts(fake_crm) == []


@pytest.mark.parametrize("typed, sent", [("1", True), ("yes", True), ("2", False), ("no", False),
                                          ("כן", True), ("לא", False), ("[clear]", None)])
def test_yes_no_field_sends_a_boolean_and_no_is_a_value_not_a_cancel(client, fake_crm, typed, sent):
    value = at_value(client, fake_crm, MEZUZAH)
    assert "1. Yes\n2. No" in value["reply"]
    fake_crm.answers.append(ok(apt()))
    confirm = turn(client, typed, value["state"])
    assert confirm["outcome"] == "confirm"
    fake_crm.answers.append(answer(apt(21, 11), MEZUZAH["field_key"], value=sent))
    turn(client, "yes" if confirm["state"]["language"] == "en" else "כן", confirm["state"])
    assert json.loads(posts(fake_crm)[-1].content) == {"field_key": MEZUZAH["field_key"], "value": sent}


def test_a_single_option_can_not_be_cleared(client, fake_crm):
    value = at_value(client, fake_crm)
    assert value["clear"] is None and all(c["message"] != "[clear]" for c in value["choices"])
    fake_crm.answers.append(ok(apt()))
    again = turn(client, "[clear]", value["state"])
    assert "can't be left empty" in again["reply"] and again["state"]["step"] == "choose_value"
    assert posts(fake_crm) == []


def test_a_long_option_list_is_numbered_and_numeric_values_are_sent_as_numbers(client, fake_crm):
    value = at_value(client, fake_crm, REPORT)
    assert len(value["choices"]) == 25 and value["choices"][22] == {"label": "23. R-23", "message": "23"}
    assert "25. R-25" in value["reply"] and "25. R-25" not in value["prompt"]
    fake_crm.answers.append(ok(apt()))
    confirm = turn(client, "23", value["state"])
    assert "from: R-03\nto: R-23" in confirm["reply"]
    fake_crm.answers.append(answer(with_value(apt(21, 11), REPORT["field_key"], 23), REPORT["field_key"]))
    saved = turn(client, "yes", confirm["state"])
    assert json.loads(posts(fake_crm)[-1].content) == {"field_key": "apartments.report_number_id", "value": 23}
    assert "Report number: R-23" in saved["reply"]


def test_a_value_not_on_the_list_is_asked_again_without_an_update(client, fake_crm):
    value = at_value(client, fake_crm)
    fake_crm.answers.append(ok(apt()))
    again = turn(client, "maybe", value["state"])
    assert "choose one of the numbers" in again["reply"] and posts(fake_crm) == []


# --- confirm and cancel -----------------------------------------------------------

def test_no_on_the_confirm_screen_keeps_the_value_and_sends_nothing(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    unclear = turn(client, "hmm, maybe", confirm["state"])
    assert unclear["outcome"] == "confirm" and "Reply \"yes\" to save exactly this change" in unclear["reply"]
    fake_crm.answers.append(ok(apt()))
    kept = turn(client, "no", confirm["state"])
    assert kept["reply"].startswith("Not changed - Jewish status stays Unknown.")
    assert kept["state"]["step"] == "choose_field" and posts(fake_crm) == []


def test_no_at_the_field_step_cancels(client, fake_crm):
    fake_crm.answers += [search(apt()), ok(apt())]
    data = turn(client, "Herzl 10")
    stop = turn(client, "cancel", data["state"])
    assert stop["outcome"] == "cancelled" and stop["show_menu"] is True and posts(fake_crm) == []


def test_a_number_for_a_list_that_changed_meanwhile_is_not_applied(client, fake_crm):
    fake_crm.answers.append(search(apt()))
    data = turn(client, "Herzl 10")
    # The admin locked a field in between: the same number would now mean another field.
    fake_crm.answers.append(ok(apt(fields=[PHONE, STATUS])))
    again = turn(client, "1", data["state"])
    assert again["outcome"] == "choose_field" and "changed since it was shown" in again["reply"]
    assert [c["label"] for c in again["choices"]] == ["1. Phone", "2. Jewish status"]


def test_unchanged_is_reported_as_unchanged_not_saved(client, fake_crm):
    confirm = at_confirm(client, fake_crm, value="3")
    fake_crm.answers.append(answer(apt(), STATUS["field_key"], status="unchanged"))
    done = turn(client, "yes", confirm["state"])
    assert done["outcome"] == "unchanged"
    assert done["reply"].startswith("Nothing changed - the CRM already had Jewish status: Unknown.")
    assert "Saved" not in done["reply"] and done["state"]["apartment_id"] == 11


def test_a_null_value_in_the_answer_is_not_invented(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    no_status = apt(21, 11, fields=[f for f in ADMIN_FIELDS if f is not STATUS])
    fake_crm.answers.append(answer(no_status, STATUS["field_key"], value=None))
    done = turn(client, "yes", confirm["state"])
    assert done["outcome"] == "updated"
    assert "no longer lets you edit Jewish status, so its value isn't shown" in done["reply"]
    assert "Not Jewish" not in done["reply"] and "Jewish status:" not in done["reply"]


# --- conflicts and lost answers --------------------------------------------------

def test_409_rereads_by_lineage_and_never_resends(client, fake_crm):
    confirm = at_confirm(client, fake_crm, apartment=apt(15, 11))
    current = with_value(apt(30, 11), STATUS["field_key"], "jewish")
    fake_crm.answers += [httpx.Response(409, json={"detail": "changed"}), ok(current)]
    again = turn(client, "yes", confirm["state"])
    assert len(posts(fake_crm)) == 1
    assert fake_crm.requests[-1].method == "GET"
    assert fake_crm.requests[-1].url.path == "/api/assistant/apartments/getapartment"
    assert fake_crm.requests[-1].url.params["id"] == "11"
    assert "changed by someone else" in again["reply"] and "Apartment #30" in again["reply"]
    assert "- Jewish status: Jewish" in again["reply"]
    assert again["state"]["step"] == "choose_value" and again["state"]["apartment_id"] == 30


def test_404_with_a_gone_lineage_ends(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers += [httpx.Response(404, json={"detail": "Apartment not found"}),
                         httpx.Response(404, json={"detail": "Apartment not found"})]
    done = turn(client, "yes", confirm["state"])
    assert done["outcome"] == "gone" and done["show_menu"] is True and len(posts(fake_crm)) == 1


def test_404_with_a_live_lineage_shows_it_and_asks_again(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers += [httpx.Response(404, json={"detail": "Apartment not found"}), ok(apt(31, 11))]
    again = turn(client, "yes", confirm["state"])
    assert "no longer current" in again["reply"] and again["state"]["apartment_id"] == 31
    assert again["state"]["step"] == "choose_value" and len(posts(fake_crm)) == 1


@pytest.mark.parametrize("lost", [
    httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("dropped"),
    httpx.Response(502, text="bad gateway"), httpx.Response(200, text="not json"),
])
def test_a_lost_answer_blocks_the_edit_and_yes_only_rereads(client, fake_crm, lost):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers.append(lost)
    blocked = turn(client, "yes", confirm["state"])
    assert blocked["outcome"] == "uncertain" and blocked["awaiting_check"] is True
    assert "won't send it again" in blocked["reply"]
    # Anything but yes / no: still blocked, nothing sent.
    before = len(fake_crm.requests)
    wait = turn(client, "1", blocked["state"])
    assert wait["outcome"] == "blocked" and len(fake_crm.requests) == before
    # yes -> a read; the CRM does not show the change.
    fake_crm.answers.append(ok(apt()))
    check = turn(client, "yes", blocked["state"])
    assert check["outcome"] == "not_confirmed" and check["state"]["step"] == "blocked"
    assert "Jewish status: Unknown. Your change (Not Jewish) does not show" in check["reply"]
    # yes again -> the CRM shows it now.
    fake_crm.answers.append(ok(with_value(apt(40, 11), STATUS["field_key"], "not_jewish")))
    check = turn(client, "yes", check["state"])
    assert check["reply"].startswith("Checked: the CRM shows the change was saved (Jewish status: Not Jewish)")
    assert check["state"]["step"] == "choose_field" and check["state"]["apartment_id"] == 40
    assert len(posts(fake_crm)) == 1  # the update was sent exactly once


def test_a_lost_answer_for_a_field_no_longer_listed_stays_unknown(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers += [httpx.ReadTimeout("slow"), ok(apt(fields=[PHONE]))]
    blocked = turn(client, "yes", confirm["state"])
    check = turn(client, "yes", blocked["state"])
    assert check["outcome"] == "not_confirmed" and check["state"]["step"] == "blocked"
    assert "can't see whether your change (Not Jewish) was saved" in check["reply"]
    assert len(posts(fake_crm)) == 1


def test_no_while_blocked_stops(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers.append(httpx.ReadTimeout("slow"))
    blocked = turn(client, "yes", confirm["state"])
    stop = turn(client, "no", blocked["state"])
    assert stop["outcome"] == "stopped" and stop["show_menu"] is True and len(posts(fake_crm)) == 1


def test_crm_unreachable_means_not_sent_and_only_a_new_yes_sends_again(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers.append(httpx.ConnectError("refused"))
    first = turn(client, "yes", confirm["state"])
    assert first["reply"].startswith("I couldn't reach the CRM, so the change was not sent")
    assert first["state"]["step"] == "confirm" and len(posts(fake_crm)) == 1
    fake_crm.answers.append(answer(with_value(apt(21, 11), STATUS["field_key"], "not_jewish"), STATUS["field_key"]))
    second = turn(client, "yes", first["state"])
    assert second["outcome"] == "updated" and len(posts(fake_crm)) == 2


def test_crm_unreachable_on_search(client, fake_crm):
    fake_crm.answers.append(httpx.ConnectError("refused"))
    data = turn(client, "Herzl 10")
    assert data["outcome"] == "crm_unreachable" and data["state"]["step"] == "address"


def test_a_double_yes_with_the_same_state_can_not_save_twice(client, fake_crm):
    """The page blocks a second tap while a turn is in flight; if two yes turns
    still arrive with the same state, the second names the retired version and
    the CRM's version check refuses it (409): one change, then a re-read."""
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers.append(answer(with_value(apt(21, 11), STATUS["field_key"], "not_jewish"), STATUS["field_key"]))
    turn(client, "yes", confirm["state"])
    fake_crm.answers += [httpx.Response(409, json={"detail": "changed"}),
                         ok(with_value(apt(21, 11), STATUS["field_key"], "not_jewish"))]
    second = turn(client, "yes", confirm["state"])
    assert [p.url.path for p in posts(fake_crm)] == ["/api/assistant/apartments/11/update"] * 2
    assert second["state"]["step"] == "choose_value" and "nothing was saved" in second["reply"]


# --- refusals --------------------------------------------------------------------

def test_401_logs_out(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers.append(httpx.Response(401, json={"detail": "Could not validate credentials"}))
    done = turn(client, "yes", confirm["state"])
    assert done["logout"] is True and done["state"]["apartment_id"] is None


@pytest.mark.parametrize("detail", ["This field cannot be edited here", "You can only edit apartments assigned to you"])
def test_403_says_the_same_thing_for_every_reason_and_reloads(client, fake_crm, detail):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers += [httpx.Response(403, json={"detail": detail}), ok(apt(fields=[PHONE]))]
    done = turn(client, "yes", confirm["state"])
    assert done["reply"].startswith("The CRM says Jewish status can't be edited here. Nothing was saved.")
    assert detail not in done["reply"]
    assert fake_crm.requests[-1].url.path == "/api/assistant/apartments/getapartment"
    assert fake_crm.requests[-1].url.params["id"] == "11"
    assert done["state"]["step"] == "choose_field" and [c["label"] for c in done["choices"]] == ["1. Phone"]


@pytest.mark.parametrize("code, text", [
    ("expected_option", "Family size: choose one of the listed values."),
    ("expected_list", "Family size: choose one or more of the listed values."),
    ("value_not_allowed", "Family size: this value is not one of the allowed values."),
    ("expected_text", "Family size: this field takes text."),
    ("expected_number", "Family size: this field takes a number"),
    ("expected_date", "Family size: this field takes a date like 2026-10-29."),
    ("expected_yes_no", "Family size: this field takes yes or no."),
    ("invalid_format", "Family size: the format is not valid."),
    ("something_new", "Family size: the value was refused (something_new)."),
])
def test_422_names_the_field_and_asks_again(client, fake_crm, code, text):
    confirm = at_confirm(client, fake_crm, SIZE, value="twelve")
    fake_crm.answers += [invalid(SIZE["field_key"], code), ok(apt())]
    again = turn(client, "yes", confirm["state"])
    assert again["reply"].startswith("The CRM did not accept the value. Nothing was saved.")
    assert text in again["reply"]
    assert again["state"]["step"] == "choose_value" and again["state"]["field_key"] == SIZE["field_key"]
    assert len(posts(fake_crm)) == 1


def test_a_bad_crm_search_shape_is_not_trusted(client, fake_crm):
    fake_crm.answers.append(ok({"results": [{"apartment_id": "11"}]}))
    data = turn(client, "Herzl")
    assert data["outcome"] == "crm_failed" and "crm_contract_error" in data["reply"]


def test_an_option_field_without_options_is_not_trusted(client, fake_crm):
    broken = dict(STATUS)
    del broken["options"]
    fake_crm.answers.append(search(apt(fields=[broken])))
    assert turn(client, "Herzl")["outcome"] == "crm_failed"


# --- language, state, token, no local field lists ----------------------------------

def test_hebrew_flow_uses_the_crms_hebrew_labels(client, fake_crm):
    apartment = apt(building_address="הרצל 10")
    fake_crm.answers += [search(apartment), ok(apartment)]
    data = turn(client, "הרצל 10", language="he")
    assert "דירה #11: הרצל 10, דירה 3, קומה 1" in data["reply"] and "1. סטטוס יהודי" in data["reply"]
    value = turn(client, "1", data["state"])
    assert "הערך הנוכחי: לא ידוע" in value["reply"] and "1. יהודי" in value["reply"]
    fake_crm.answers.append(ok(apartment))
    confirm = turn(client, "יהודי", value["state"])
    assert "מ: לא ידוע\nל: יהודי" in confirm["reply"]
    assert [c["message"] for c in confirm["choices"]] == ["כן", "לא"]
    fake_crm.answers.append(answer(with_value(apt(21, 11, building_address="הרצל 10"), STATUS["field_key"],
                                              "jewish"), STATUS["field_key"]))
    saved = turn(client, "כן", confirm["state"])
    assert saved["reply"].startswith("נשמר. ב-CRM מופיע עכשיו סטטוס יהודי: יהודי.")


def test_state_with_extra_keys_is_refused(client, fake_crm):
    body = {"intent": "edit_apartment", "message": "1",
            "apartment_state": {"step": "choose_field", "editable_fields": []}}
    assert client.post("/assistant/turn", json=body, headers=AUTH).status_code == 422


def test_the_state_holds_no_field_list_and_no_token(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers.append(answer(with_value(apt(21, 11), STATUS["field_key"], "jewish"), STATUS["field_key"]))
    saved = turn(client, "yes", confirm["state"])
    for data in (confirm, saved):
        held = json.dumps(data["state"], ensure_ascii=False)
        assert "editable_fields" not in held and "options" not in held
        assert "Language" not in held and "Russian" not in held
        assert TOKEN not in json.dumps(data)


def test_no_field_name_label_or_option_is_kept_in_the_assistant_code():
    code = "\n".join((ROOT / path).read_text(encoding="utf-8")
                     for path in ("app/apartment_chat.py", "app/crm_api.py", "app/main.py", "static/index.html"))
    # The language toggle's own labels (docs/instructions19.md) are the same words as the fixture's
    # language options; they are the page's text, not CRM options.
    code = code.replace('other_language: "עברית"', "").replace('other_language: "English"', "")
    # Quoted: a string literal for a key, label or option (plain words like "English" appear in page text).
    for field in ADMIN_FIELDS:
        assert field["field_key"] not in code
        assert f'"{field["label_en"]}"' not in code and f'"{field["label"]}"' not in code
        for option in field.get("options", []):
            for text in (option["value"], option["label"], option["label_en"]):
                if isinstance(text, str):  # report numbers' values are ids, not names
                    assert f'"{text}"' not in code
    for old in ("jewish_status", "Jewish_status", "label_he", "/apartments/fields", "mezuzah", "report_number"):
        assert old not in code


# --- labels by the user's language (docs/instructions19.md) ----------------------------

ONE_LANGUAGE = {"field_key": "customfield.apartment.notes_x", "label": "הערות מיוחדות", "label_en": None,
                "kind": "single_option", "current_value": "b",
                "options": [{"value": "a", "label": None, "label_en": "Only English"},
                            {"value": "b", "label": "רק עברית", "label_en": None},
                            {"value": "c-7", "label": None, "label_en": None}]}
ENGLISH_ONLY = {"field_key": "customfield.apartment.code_y", "label": None, "label_en": "Gate code",
                "kind": "text", "current_value": "A-17"}


@pytest.mark.parametrize("language", ["en", "he"])
def test_labels_follow_the_language_and_one_language_items_show_as_they_are(client, fake_crm, language):
    apartment = apt(apartment_id=4071, lineage_id=902, fields=[STATUS, ONE_LANGUAGE, ENGLISH_ONLY])
    fake_crm.answers += [search(apartment), ok(apartment)]
    data = turn(client, "Herzl", language=language)
    # label_en in English, label in Hebrew; a label in one language only is shown as it is.
    expected = (["1. Jewish status", "2. הערות מיוחדות", "3. Gate code"] if language == "en"
                else ["1. סטטוס יהודי", "2. הערות מיוחדות", "3. Gate code"])
    for line in expected:
        assert line in data["reply"]
    assert "#4071" in data["reply"] and "Herzl 10, Tel Aviv" in data["reply"]  # IDs and CRM values as given
    assert data["state"]["apartment_id"] == 4071 and data["state"]["lineage_id"] == 902
    fake_crm.answers.append(ok(apartment))
    value = turn(client, "2", data["state"], language=language)
    # Options: same rule; one with no label at all shows its value; the value sent is the CRM's.
    assert [c["label"] for c in value["choices"]] == ["1. Only English", "2. רק עברית", "3. c-7"]
    assert [c["message"] for c in value["choices"]] == ["1", "2", "3"]
    fake_crm.answers.append(ok(apartment))
    confirm = turn(client, "3", value["state"], language=language)
    fake_crm.answers.append(answer(with_value(apt(4072, 902, fields=[STATUS, ONE_LANGUAGE, ENGLISH_ONLY]),
                                              ONE_LANGUAGE["field_key"], "c-7"), ONE_LANGUAGE["field_key"],
                                   previous=4071))
    turn(client, "yes" if language == "en" else "כן", confirm["state"], language=language)
    assert json.loads(posts(fake_crm)[-1].content) == {"field_key": "customfield.apartment.notes_x", "value": "c-7"}
    assert posts(fake_crm)[-1].url.path == "/api/assistant/apartments/4071/update"
