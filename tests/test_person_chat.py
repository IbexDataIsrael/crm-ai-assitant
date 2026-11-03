"""A person in the chat (POST /assistant/turn, intent person): open, edit one field, record a visit.

The CRM is a mock transport (fake_crm); nothing reaches a real CRM or AI
provider. CRM answers copy the shapes of sheepsidebay_crm
backend/app/routers/assistant_contacts.py and contact_edit_rules.py (fixes117,
2026-11-02): one `label` per field and option in the user's language,
`editable_fields`, `new_visit_fields`, `recent_visits`, `can_add_visit`,
`web_path`, and `request_id` / `replayed` on every write.
"""

import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import CRM_ME
from tests.test_apartment_chat import AUTH, TOKEN, UUID, ok

CONTACT_PATH = "/api/assistant/contacts/12"

# editable_fields as contact_edit_rules.entry builds them.
NAME = {"field_key": "contacts.name", "label": "First name", "kind": "text", "current_value": "Yosef"}
LAST = {"field_key": "contacts.last_name", "label": "Last name", "kind": "text", "current_value": "Cohen"}
EMAIL = {"field_key": "contacts.email", "label": "Email", "kind": "text", "current_value": None}
STATUS = {"field_key": "contacts.pipeline_status", "label": "Status", "kind": "single_option",
          "current_value": "new", "options": [{"value": "new", "label": "New"},
                                              {"value": "warm", "label": "Warm"}]}
PRIORITY = {"field_key": "contacts.priority_score", "label": "Priority score", "kind": "number",
            "current_value": 3}
BIRTHDAY = {"field_key": "customfield.contact_mekurav.birthday", "label": "Birthday", "kind": "date",
            "current_value": "1980-05-01"}
SHABBAT = {"field_key": "customfield.contact_mekurav.keeps_shabbat", "label": "Keeps Shabbat", "kind": "yes_no",
           "current_value": False}
FIELDS = [NAME, LAST, EMAIL, STATUS, PRIORITY, BIRTHDAY, SHABBAT]

# new_visit_fields as contact_edit_rules.visit_field_entry builds them.
V_DATE = {"field_key": "visits.visit_date", "label": "Date", "kind": "date", "required": True,
          "default": "2026-11-02"}
V_TYPE = {"field_key": "visits.visit_type", "label": "Visit type", "kind": "single_option",
          "options": [{"value": "home", "label": "Home visit"}, {"value": "phone", "label": "Phone call"}]}
V_LOCATION = {"field_key": "visits.location", "label": "Location", "kind": "text"}
V_TORAH = {"field_key": "visits.torah_thought", "label": "Torah thought", "kind": "single_option",
           "options": [{"value": "given", "label": "Given"}, {"value": "planned", "label": "Planned"}]}
V_TEFILLIN = {"field_key": "customfield.visit.tefillin", "label": "Tefillin", "kind": "yes_no", "default": False}
V_REBBE = {"field_key": "customfield.visit.rebbe", "label": "Connection to the Rebbe", "kind": "text"}
V_CANDLES = {"field_key": "customfield.visit.candles", "label": "Shabbat candles", "kind": "yes_no",
             "default": False}
V_TZEDAKA = {"field_key": "customfield.visit.tzedaka", "label": "Tzedaka", "kind": "yes_no", "default": False}
V_NOTES = {"field_key": "visits.notes", "label": "Notes", "kind": "text"}
V_LOG = {"field_key": "visits.interactions", "label": "Interactions", "kind": "entries", "default": [],
         "options": [{"value": "call", "label": "Call"}, {"value": "mezuza", "label": "Mezuza check"}]}
VISIT_FIELDS = [V_DATE, V_TYPE, V_LOCATION, V_TORAH, V_TEFILLIN, V_REBBE, V_CANDLES, V_TZEDAKA, V_NOTES, V_LOG]
OLD_VISIT = {"visit_id": 301, "visit_date": "2026-10-20", "visit_type": "Home visit",
             "done": ["Tefillin", "Torah thought: Given"], "notes": None}


def contact(fields=None, visit_fields=None, status="active", **values):
    return {"contact_id": 12, "status": status, "status_label": "Draft" if status == "draft" else None,
            "name": "Yosef", "last_name": "Cohen", "email": None, "address": "Herzl 10",
            "web_path": "/dashboard/contacts/12",
            "editable_fields": [dict(f) for f in (FIELDS if fields is None else fields)],
            "can_add_visit": status == "active",
            "new_visit_fields": [dict(f) for f in (VISIT_FIELDS if visit_fields is None else visit_fields)],
            "recent_visits": [OLD_VISIT], **values}


def with_value(view, field_key, value):
    for entry in view["editable_fields"]:
        if entry["field_key"] == field_key:
            entry["current_value"] = value
    return view


def updated(view, field_key, status="updated", replayed=False):
    value = next((f["current_value"] for f in view["editable_fields"] if f["field_key"] == field_key), None)
    return ok({"status": status, "replayed": replayed, "field_key": field_key, "value": value,
               "contact_id": 12, "contact": view})


def created(visit_id=401, replayed=False, done=("Tefillin", "Shabbat candles")):
    return httpx.Response(201, json={"status": "created", "replayed": replayed, "visit_id": visit_id,
                                     "contact_id": 12,
                                     "visit": {"visit_id": visit_id, "visit_date": "2026-11-02",
                                               "visit_type": "Home visit", "done": list(done), "notes": None}})


def invalid(field_key, code, **extra):
    return httpx.Response(422, json={"detail": {"status": "invalid", "invalid_fields": [
        {"field": field_key, "code": code, **extra}]}})


@pytest.fixture
def client():
    return TestClient(app)


def turn(client, message="", state=None, open_id=None, language=None):
    body = {"intent": "person", "message": message}
    if state is not None:
        body["person_state"] = state
    if open_id is not None:
        body["open_id"] = open_id
    if language:
        body["language"] = language
    response = client.post("/assistant/turn", json=body, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def posts(fake_crm):
    return [r for r in fake_crm.requests if r.method == "POST"]


def body_of(post):
    body = json.loads(post.content)
    assert UUID.match(body.pop("request_id"))
    return body


def opened(client, fake_crm, view=None):
    fake_crm.answers.append(ok(view or contact()))
    return turn(client, open_id=12)


def at_value(client, fake_crm, field, view=None):
    view = view or contact()
    data = opened(client, fake_crm, view)
    fake_crm.answers += [ok(view), ok(view)]
    fields = turn(client, "[edit]", data["state"])
    return turn(client, str(FIELDS.index(field) + 1), fields["state"])


def at_confirm(client, fake_crm, field=STATUS, value="2", view=None):
    view = view or contact()
    data = at_value(client, fake_crm, field, view)
    fake_crm.answers.append(ok(view))
    confirm = turn(client, value, data["state"])
    assert confirm["outcome"] == "confirm", confirm["reply"]
    return confirm


# --- opening ------------------------------------------------------------------------

def test_open_reads_the_contact_and_offers_edit_or_visit(client, fake_crm, monkeypatch):
    monkeypatch.setenv("CRM_WEB_URL", "https://crm.example.org")
    data = opened(client, fake_crm)
    (request,) = fake_crm.requests
    assert request.method == "GET" and request.url.path == CONTACT_PATH
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert data["intent"] == "person" and data["outcome"] == "menu"
    # Filled fields only, then how many are empty (the edit list shows them all).
    assert data["reply"].startswith("Yosef Cohen\n- First name: Yosef\n- Last name: Cohen\n- Status: New")
    assert "- Keeps Shabbat: No" in data["reply"] and "Email" not in data["reply"]
    assert '(1 more field is empty - choose "Edit a field" to fill it.)' in data["reply"]
    assert "Recent visits:\n- 2026-10-20 - Home visit: Tefillin, Torah thought: Given" in data["reply"]
    assert data["choices"] == [{"label": "Edit a field", "message": "[edit]"},
                               {"label": "Record a visit", "message": "[visit]"}]
    # The link is CRM_WEB_URL + the CRM's own web_path, never built from the id here.
    assert data["link"] == {"label": "Open in the CRM", "url": "https://crm.example.org/dashboard/contacts/12"}
    assert data["state"] == {"step": "menu", "language": "en", "contact_id": 12, "field_key": None, "shown": None,
                             "pending": None, "visit": None}


def test_a_draft_is_labelled_and_not_offered_a_visit(client, fake_crm):
    view = contact(status="draft")
    data = opened(client, fake_crm, view)
    assert data["reply"].startswith("Yosef Cohen [Draft]")
    assert [c["message"] for c in data["choices"]] == ["[edit]"]
    assert "once this contact is approved" in data["reply"]
    fake_crm.answers.append(ok(view))
    again = turn(client, "[visit]", data["state"])  # a stale button: still no visit
    assert again["outcome"] == "menu" and posts(fake_crm) == []


@pytest.mark.parametrize("answer, outcome", [
    (httpx.Response(404, json={"detail": "Contact not found"}), "gone"),
    (httpx.Response(403, json={"detail": "Not available for the platform superadmin"}), "forbidden"),
    (httpx.Response(401, json={"detail": "Could not validate credentials"}), "unauthorized"),
    (httpx.ConnectError("refused"), "crm_unreachable"),
    (ok({"contact_id": 12}), "crm_failed"),
])
def test_open_failures(client, fake_crm, answer, outcome):
    fake_crm.answers.append(answer)
    data = turn(client, open_id=12)
    assert data["outcome"] == outcome and data["logout"] is (outcome == "unauthorized")
    if outcome == "forbidden":
        assert data["reply"] == "The CRM did not allow this request. Nothing was changed."


@pytest.mark.parametrize("role", ["shliach", "campaign_manager", None])
def test_other_roles_never_reach_a_person(client, fake_crm, role):
    me = {k: v for k, v in CRM_ME.items() if k != "role"}
    if role:
        me["role"] = role
    fake_crm.me_answers.append(httpx.Response(200, json=me))
    data = turn(client, "[edit]", {"step": "menu", "language": "en", "contact_id": 12})
    assert data["outcome"] == "forbidden" and fake_crm.requests == []


def test_a_hidden_field_is_simply_not_shown(client, fake_crm):
    view = contact(fields=[NAME, LAST])
    del view["email"]  # the org hides it: the key is missing, not null
    data = opened(client, fake_crm, view)
    assert "Email" not in data["reply"] and "Status" not in data["reply"]


# --- editing one field ----------------------------------------------------------------

def test_edit_lists_the_crms_fields_then_confirms_then_shows_the_read_back(client, fake_crm):
    data = opened(client, fake_crm)
    fake_crm.answers.append(ok(contact()))
    fields = turn(client, "[edit]", data["state"])
    assert fields["outcome"] == "choose_field"
    assert [c["label"] for c in fields["choices"]] == [
        "1. First name", "2. Last name", "3. Email", "4. Status", "5. Priority score", "6. Birthday",
        "7. Keeps Shabbat"]
    fake_crm.answers.append(ok(contact()))
    value = turn(client, "4", fields["state"])
    assert "Status - current value: New" in value["reply"] and value["clear"] is None
    fake_crm.answers.append(ok(contact()))
    confirm = turn(client, "Warm", value["state"])
    assert "Change Status of Yosef Cohen:\nfrom: New\nto: Warm" in confirm["reply"]
    assert posts(fake_crm) == []  # nothing before the yes
    fake_crm.answers.append(updated(with_value(contact(), STATUS["field_key"], "warm"), STATUS["field_key"]))
    saved = turn(client, "yes", confirm["state"])
    (post,) = posts(fake_crm)
    assert post.url.path == CONTACT_PATH + "/update"
    assert body_of(post) == {"field_key": "contacts.pipeline_status", "value": "warm"}
    assert saved["outcome"] == "updated"
    assert saved["reply"].startswith("Saved. The CRM now shows Status: Warm.\nYosef Cohen")
    assert saved["state"]["step"] == "menu" and saved["state"]["pending"] is None


@pytest.mark.parametrize("field, typed, sent_value, shown", [
    (EMAIL, "yosef@example.org", "yosef@example.org", "yosef@example.org"),
    (EMAIL, "[clear]", None, "-"),
    (PRIORITY, "7", "7", "7"),
    (PRIORITY, "[clear]", None, "-"),
    (BIRTHDAY, "2/3/1981", "1981-03-02", "1981-03-02"),
    (BIRTHDAY, "[clear]", None, "-"),
    (SHABBAT, "yes", True, "Yes"),
    (SHABBAT, "2", False, "No"),
    (STATUS, "1", "new", "New"),
])
def test_each_kind_is_sent_as_the_crm_expects(client, fake_crm, field, typed, sent_value, shown):
    value = at_value(client, fake_crm, field)
    fake_crm.answers.append(ok(contact()))
    confirm = turn(client, typed, value["state"])
    assert f"to: {shown}" in confirm["reply"]
    fake_crm.answers.append(updated(contact(), field["field_key"]))
    turn(client, "yes", confirm["state"])
    assert body_of(posts(fake_crm)[-1]) == {"field_key": field["field_key"], "value": sent_value}


@pytest.mark.parametrize("field, clearable", [(NAME, True), (EMAIL, True), (PRIORITY, True), (BIRTHDAY, True),
                                              (STATUS, False), (SHABBAT, False)])
def test_only_text_number_and_date_offer_clearing(client, fake_crm, field, clearable):
    value = at_value(client, fake_crm, field)
    assert (value["clear"] is not None) is clearable
    if not clearable:
        fake_crm.answers.append(ok(contact()))
        again = turn(client, "[clear]", value["state"])
        assert "can't be left empty" in again["reply"] and posts(fake_crm) == []


def test_clearing_the_first_name_is_refused_by_the_crm_and_said_plainly(client, fake_crm):
    confirm = at_confirm(client, fake_crm, NAME, "[clear]")
    fake_crm.answers += [invalid("contacts.name", "required"), ok(contact())]
    again = turn(client, "yes", confirm["state"])
    assert "The CRM did not accept the value. Nothing was saved.\nFirst name: this field can't be left empty." \
        in again["reply"]
    assert again["state"]["step"] == "choose_value"


@pytest.mark.parametrize("code, extra, text", [
    ("required", {}, "Email: this field can't be left empty."),
    ("too_long", {"max_length": 255}, "Email: the value is too long (at most 255 characters)."),
    ("invalid_phone", {}, "Email: this is not a valid phone number."),
    ("phone_taken", {}, "Email: another contact already has this phone number."),
    ("invalid_email", {}, "Email: this is not a valid email address."),
    ("expected_text", {}, "Email: this field takes text."),
    ("expected_number", {}, "Email: this field takes a number"),
    ("expected_whole_number", {}, "Email: this field takes a whole number."),
    ("expected_date", {}, "Email: this field takes a date like 2026-10-29."),
    ("expected_option", {}, "Email: choose one of the listed values."),
    ("expected_yes_no", {}, "Email: this field takes yes or no."),
    ("value_not_allowed", {}, "Email: this value is not one of the allowed values."),
])
def test_each_422_code_names_the_field(client, fake_crm, code, extra, text):
    confirm = at_confirm(client, fake_crm, EMAIL, "x")
    fake_crm.answers += [invalid(EMAIL["field_key"], code, **extra), ok(contact())]
    again = turn(client, "yes", confirm["state"])
    assert text in again["reply"] and "Nothing was saved" in again["reply"]
    assert len(posts(fake_crm)) == 1


def test_request_id_reused_says_nothing_was_saved(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers += [invalid("request_id", "request_id_reused"), ok(contact())]
    done = turn(client, "yes", confirm["state"])
    assert "already used for a different change. Nothing was saved." in done["reply"]
    assert done["state"]["step"] == "menu" and done["state"]["pending"] is None


def test_403_is_one_fixed_message_and_the_person_is_reloaded(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers += [httpx.Response(403, json={"detail": "This field cannot be edited here"}),
                         ok(contact(fields=[NAME]))]
    done = turn(client, "yes", confirm["state"])
    assert done["reply"].startswith("The CRM did not allow this request. Nothing was changed.")
    assert "cannot be edited here" not in done["reply"]
    assert done["state"]["step"] == "menu" and "Status" not in done["reply"]


def test_404_on_save_means_the_person_is_gone(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers.append(httpx.Response(404, json={"detail": "Contact not found"}))
    done = turn(client, "yes", confirm["state"])
    assert done["outcome"] == "gone" and done["show_menu"] is True


def test_a_lost_answer_blocks_and_yes_resends_the_same_request(client, fake_crm):
    confirm = at_confirm(client, fake_crm)
    fake_crm.answers.append(httpx.ReadTimeout("slow"))
    blocked = turn(client, "yes", confirm["state"])
    assert blocked["outcome"] == "uncertain" and blocked["awaiting_check"] is True
    fake_crm.answers.append(updated(with_value(contact(), STATUS["field_key"], "warm"), STATUS["field_key"],
                                    replayed=True))
    check = turn(client, "yes", blocked["state"])
    first, second = posts(fake_crm)
    assert json.loads(first.content) == json.loads(second.content)
    assert check["reply"].startswith("Checked: the CRM confirms the change was saved. It now shows Status: Warm.")
    assert fake_crm.requests[-1] is second  # no read in between: only the CRM's record is proof


def test_a_number_for_a_field_list_that_changed_is_not_applied(client, fake_crm):
    data = opened(client, fake_crm)
    fake_crm.answers.append(ok(contact()))
    fields = turn(client, "[edit]", data["state"])
    fake_crm.answers.append(ok(contact(fields=[STATUS, NAME])))
    again = turn(client, "1", fields["state"])
    assert again["outcome"] == "choose_field" and "changed in the CRM" in again["reply"]
    assert [c["label"] for c in again["choices"]] == ["1. Status", "2. First name"]


# --- recording a visit ------------------------------------------------------------------

def start_visit(client, fake_crm, view=None):
    view = view or contact()
    data = opened(client, fake_crm, view)
    fake_crm.answers.append(ok(view))
    return turn(client, "[visit]", data["state"])


def step(client, fake_crm, state, message, view=None):
    fake_crm.answers.append(ok(view or contact()))
    return turn(client, message, state)


def test_a_full_visit_one_confirm_one_call(client, fake_crm):
    q = start_visit(client, fake_crm)
    assert q["outcome"] == "visit" and "Recording a visit for Yosef Cohen" in q["reply"]
    assert "Date - choose \"Today (2026-11-02)\"" in q["reply"]
    # The first question has no "done" button: a visit is never saved with nothing but a date by one tap.
    assert q["choices"] == [{"label": "Today (2026-11-02)", "message": "[default]"}]
    q = step(client, fake_crm, q["state"], "[default]")
    # Then what was done: every yes/no field (the mitzvot) at once ...
    assert q["multi"] is True and "What was done?" in q["reply"]
    assert [c["label"] for c in q["choices"]] == ["1. Tefillin", "2. Shabbat candles", "3. Tzedaka"]
    assert q["clear"] == {"label": "None of these", "message": "[skip]"}
    q = step(client, fake_crm, q["state"], "1,2")
    # ... and the interactions, from the CRM's own categories.
    assert q["multi"] is True and q["prompt"].startswith("Interactions - what happened during the visit?")
    assert [(c["label"], c["checked"]) for c in q["choices"]] == [("1. Call", False), ("2. Mezuza check", False)]
    q = step(client, fake_crm, q["state"], "2")
    assert q["prompt"].startswith("Visit type:") and [c["message"] for c in q["choices"]] == ["1", "2", "[skip]",
                                                                                               "[finish]"]
    assert q["choices"][-1]["label"] == "Done - review the visit"
    q = step(client, fake_crm, q["state"], "1")
    assert "Location - type it, or skip" in q["reply"]
    q = step(client, fake_crm, q["state"], "[skip]")
    assert q["prompt"].startswith("Torah thought:")
    q = step(client, fake_crm, q["state"], "Given")
    assert "Connection to the Rebbe - type it" in q["reply"]
    q = step(client, fake_crm, q["state"], "Learning Tanya weekly")
    assert "Notes - type it" in q["reply"]
    confirm = step(client, fake_crm, q["state"], "Warm visit")
    assert confirm["outcome"] == "visit_confirm"
    assert confirm["reply"] == (
        "Record a visit for Yosef Cohen:\n- Date: 2026-11-02\n- Visit type: Home visit\n- Torah thought: Given\n"
        "- Tefillin ✓\n- Connection to the Rebbe: Learning Tanya weekly\n- Shabbat candles ✓\n- Notes: Warm visit"
        "\n- Interactions: Mezuza check"
        "\n\nReply \"yes\" to save this visit, or \"no\" to cancel.")
    assert posts(fake_crm) == []
    fake_crm.answers += [ok(contact()), created(), ok(contact())]
    saved = turn(client, "yes", confirm["state"])
    (post,) = posts(fake_crm)
    assert post.url.path == CONTACT_PATH + "/visits"
    assert body_of(post) == {"values": {
        "visits.visit_date": "2026-11-02", "visits.visit_type": "home", "visits.torah_thought": "given",
        "customfield.visit.tefillin": True, "customfield.visit.rebbe": "Learning Tanya weekly",
        "customfield.visit.candles": True, "visits.notes": "Warm visit",
        "visits.interactions": [{"category": "mezuza", "note": ""}]}}
    assert saved["outcome"] == "visit_created"
    assert saved["reply"].startswith(
        "Saved. The CRM recorded the visit: 2026-11-02, Home visit, Tefillin, Shabbat candles.")
    assert saved["state"]["step"] == "menu" and saved["state"]["visit"] is None


def test_finish_early_sends_the_date_only(client, fake_crm):
    q = start_visit(client, fake_crm)
    confirm = step(client, fake_crm, q["state"], "[finish]")
    assert confirm["reply"].startswith("Record a visit for Yosef Cohen:\n- Date: 2026-11-02\n\n")
    fake_crm.answers += [ok(contact()), created(done=()), ok(contact())]
    turn(client, "yes", confirm["state"])
    assert body_of(posts(fake_crm)[0]) == {"values": {"visits.visit_date": "2026-11-02"}}


def test_a_typed_day_first_date_and_an_impossible_one(client, fake_crm):
    q = start_visit(client, fake_crm)
    bad = step(client, fake_crm, q["state"], "31/02/2026")
    assert "not a real date" in bad["reply"] and bad["state"]["visit"]["position"] == 0
    bad = step(client, fake_crm, q["state"], "yesterday")  # a visit date must be a real date
    assert "not a real date" in bad["reply"]
    q = step(client, fake_crm, q["state"], "30/10/2026")
    confirm = step(client, fake_crm, q["state"], "[finish]")
    assert "- Date: 2026-10-30" in confirm["reply"]


def test_nothing_ticked_and_skips_leave_the_mitzvot_out(client, fake_crm):
    q = start_visit(client, fake_crm, contact(visit_fields=[V_DATE, V_TEFILLIN, V_CANDLES]))
    view = contact(visit_fields=[V_DATE, V_TEFILLIN, V_CANDLES])
    q = step(client, fake_crm, q["state"], "[default]", view)
    confirm = step(client, fake_crm, q["state"], "[skip]", view)
    assert "✓" not in confirm["reply"]
    fake_crm.answers += [ok(view), created(done=()), ok(view)]
    turn(client, "yes", confirm["state"])
    assert body_of(posts(fake_crm)[0]) == {"values": {"visits.visit_date": "2026-11-02"}}


def test_no_on_the_visit_confirm_saves_nothing(client, fake_crm):
    q = start_visit(client, fake_crm)
    confirm = step(client, fake_crm, q["state"], "[finish]")
    fake_crm.answers.append(ok(contact()))
    done = turn(client, "no", confirm["state"])
    assert done["reply"].startswith("The visit was not recorded.") and posts(fake_crm) == []


def test_a_visit_form_that_changed_starts_the_questions_again(client, fake_crm):
    q = start_visit(client, fake_crm)
    q = step(client, fake_crm, q["state"], "[default]")
    again = step(client, fake_crm, q["state"], "1", contact(visit_fields=[V_DATE, V_NOTES]))
    assert again["reply"].startswith("The visit form changed in the CRM meanwhile")
    assert again["state"]["visit"]["position"] == 0 and again["state"]["visit"]["values"] == {}


def test_a_draft_answered_409_says_approve_first(client, fake_crm):
    q = start_visit(client, fake_crm)
    confirm = step(client, fake_crm, q["state"], "[finish]")
    fake_crm.answers += [ok(contact()), httpx.Response(409, json={"detail": "Approve the contact first"}),
                         ok(contact())]
    done = turn(client, "yes", confirm["state"])
    assert done["reply"].startswith("The CRM records visits only for an approved contact. Nothing was saved.")


def test_a_lost_visit_answer_is_resent_with_the_same_request_and_the_replay_proves_it(client, fake_crm):
    q = start_visit(client, fake_crm)
    confirm = step(client, fake_crm, q["state"], "[finish]")
    fake_crm.answers += [ok(contact()), httpx.Response(502, text="bad gateway")]
    blocked = turn(client, "yes", confirm["state"])
    assert blocked["outcome"] == "uncertain" and blocked["state"]["step"] == "visit_blocked"
    assert "don't know yet whether the visit was saved" in blocked["reply"]
    wait = turn(client, "1", blocked["state"])
    assert wait["outcome"] == "blocked" and "earlier visit" in wait["reply"]
    fake_crm.answers += [created(replayed=True), ok(contact())]
    check = turn(client, "yes", blocked["state"])
    first, second = posts(fake_crm)
    assert json.loads(first.content) == json.loads(second.content)  # same request_id, same values
    assert check["outcome"] == "visit_created" and check["reply"].startswith("Saved. The CRM recorded the visit")


def test_a_refused_visit_value_names_the_field_by_its_label(client, fake_crm):
    q = start_visit(client, fake_crm)
    confirm = step(client, fake_crm, q["state"], "[finish]")
    fake_crm.answers += [ok(contact()), invalid("visits.visit_date", "expected_date"), ok(contact())]
    done = turn(client, "yes", confirm["state"])
    assert "The CRM did not accept the visit. Nothing was saved.\nDate: this field takes a date" in done["reply"]


def test_a_visit_not_sent_can_be_sent_again_with_the_same_request(client, fake_crm):
    q = start_visit(client, fake_crm)
    confirm = step(client, fake_crm, q["state"], "[finish]")
    fake_crm.answers += [ok(contact()), httpx.ConnectError("refused")]
    first = turn(client, "yes", confirm["state"])
    assert first["outcome"] == "not_sent" and first["state"]["step"] == "visit_confirm"
    fake_crm.answers += [created(), ok(contact())]
    turn(client, "yes", first["state"])
    one, two = posts(fake_crm)
    assert json.loads(one.content) == json.loads(two.content)


# --- language, state, labels ---------------------------------------------------------------

def test_hebrew_wording_with_the_crms_hebrew_labels(client, fake_crm):
    he_status = {"field_key": "contacts.pipeline_status", "label": "סטטוס", "kind": "single_option",
                 "current_value": "new", "options": [{"value": "new", "label": "חדש"},
                                                     {"value": "warm", "label": "חם"}]}
    view = contact(fields=[he_status], name="יוסף", last_name="כהן")
    fake_crm.answers.append(ok(view))
    data = turn(client, open_id=12, language="he")
    assert data["reply"].startswith("יוסף כהן\n- סטטוס: חדש")
    assert [c["label"] for c in data["choices"]] == ["עריכת שדה", "רישום ביקור"]
    fake_crm.answers += [ok(view), ok(view), ok(view)]
    fields = turn(client, "[edit]", data["state"])
    value = turn(client, "1", fields["state"])
    confirm = turn(client, "חם", value["state"])
    assert "שינוי סטטוס של יוסף כהן:\nמ: חדש\nל: חם" in confirm["reply"]


def test_the_state_holds_no_field_list_no_labels_and_no_token(client, fake_crm):
    q = start_visit(client, fake_crm)
    q = step(client, fake_crm, q["state"], "[default]")
    held = json.dumps(q["state"], ensure_ascii=False)
    for word in ("editable_fields", "options", "Home visit", "Tefillin", "First name", TOKEN):
        assert word not in held


def test_person_state_with_extra_keys_is_refused(client, fake_crm):
    body = {"intent": "person", "message": "1",
            "person_state": {"step": "menu", "contact_id": 12, "editable_fields": []}}
    assert client.post("/assistant/turn", json=body, headers=AUTH).status_code == 422


def test_no_contact_or_visit_field_name_is_kept_in_the_assistant_code():
    from tests.test_apartment_chat import ROOT
    code = "\n".join((ROOT / path).read_text(encoding="utf-8")
                     for path in ("app/person_chat.py", "app/crm_api.py", "app/main.py", "static/index.html"))
    for field in FIELDS + VISIT_FIELDS:
        assert f'"{field["field_key"]}"' not in code and f'"{field["label"]}"' not in code
    assert not re.search(r"dashboard/contacts", code)
