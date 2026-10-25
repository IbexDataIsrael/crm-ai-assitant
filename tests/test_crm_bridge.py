"""CRM contact-draft bridge: /crm/contact-drafts/proposals and .../{id}/confirm.

The CRM is always a mock transport (httpx.MockTransport); nothing reaches a
real CRM or AI provider. The CRM answers here copy the shapes in
sheepsidebay_crm backend/app/routers/assistant.py.
"""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app import crm_api, generation
from app.main import app
from tests.conftest import REAL_MAKE_CRM_CLIENT

CRM_URL = "http://crm.test:8001/"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.secret-user-token.sig"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PROPOSAL_ID = "0b6f2c1e-8a4d-4c1b-9e2f-3a5d7c9e1f20"
PAYLOAD_HASH = "a" * 64
DRAFT_FIELDS = {"first_name": "Dana", "last_name": "Levi", "phone": "0521234567", "email": None, "address": None}

NEEDS_INFORMATION = {
    "status": "needs_information",
    "action": "create_contact_draft",
    "missing_fields": ["last_name"],
    "invalid_fields": [],
    "fields": {"first_name": "Dana", "last_name": None, "phone": "0521234567", "email": None, "address": None},
}
PROPOSAL = {
    "status": "proposal",
    "action": "create_contact_draft",
    "proposal_id": PROPOSAL_ID,
    "payload_hash": PAYLOAD_HASH,
    "expires_at": "2026-10-25T12:15:00Z",
    "draft": {"contact_type": "mekurav", "status": "draft", "source": "assistant",
              "assigned_user_id": "user-guid-1", **DRAFT_FIELDS},
}


def created(replayed):
    return {
        "status": "created",
        "replayed": replayed,
        "proposal_id": PROPOSAL_ID,
        "contact_id": 42,
        "contact": {"id": 42, "status": "draft", "contact_type": "mekurav", "source": "assistant",
                    **DRAFT_FIELDS, "assigned_user_id": "user-guid-1", "created_at": "2026-10-25T12:01:00"},
    }


class FakeCRM:
    """Answers each request with the next item: an httpx.Response, or an exception to raise."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def client():
    # No "with": the lifespan (embedding model + vector store) is not needed here.
    return TestClient(app)


@pytest.fixture
def crm(monkeypatch):
    """Configure CRM_API_URL and route the bridge's HTTP client to a FakeCRM."""
    monkeypatch.setenv("CRM_API_URL", CRM_URL)
    fake = FakeCRM()

    def make(base_url):
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(fake.handler),
                            follow_redirects=False)

    monkeypatch.setattr(crm_api, "make_http_client", make)
    return fake


@pytest.fixture(autouse=True)
def no_ai_calls(monkeypatch):
    """The bridge must never hand anything (least of all the token) to an AI provider."""
    def refuse(*args, **kwargs):
        raise AssertionError("The CRM bridge called answer generation")

    monkeypatch.setattr(generation, "generate_answer", refuse)


def propose(client, fields, headers=AUTH):
    return client.post("/crm/contact-drafts/proposals", json={"fields": fields}, headers=headers)


def confirm(client, body=None, proposal_id=PROPOSAL_ID, headers=AUTH):
    body = {"payload_hash": PAYLOAD_HASH} if body is None else body
    return client.post(f"/crm/contact-drafts/proposals/{proposal_id}/confirm", json=body, headers=headers)


# --- configuration -----------------------------------------------------------

def test_missing_crm_url_fails_clearly(client, monkeypatch):
    response = propose(client, {"first_name": "Dana"})
    assert response.status_code == 503
    assert response.json() == {"status": "crm_not_configured", "message": "CRM_API_URL is not set"}
    assert confirm(client).json()["status"] == "crm_not_configured"


@pytest.mark.parametrize("value, message", [
    ("http://admin:hunter2@crm.test:8001/", "must not contain credentials"),
    ("http://hunter2@crm.test:8001/", "must not contain credentials"),
    ("ftp://crm.test/", "http:// or https://"),
    ("crm.test:8001", "http:// or https://"),
    ("http:///api", "http:// or https://"),
    ("http://crm.test:99999/", "not a valid address"),
    ("http://crm.test:8001/?next=evil", "query or fragment"),
])
def test_invalid_crm_url_is_rejected_without_echoing_it(client, monkeypatch, value, message):
    monkeypatch.setenv("CRM_API_URL", value)
    response = propose(client, {"first_name": "Dana"})
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "crm_not_configured"
    assert message in body["message"]
    assert "hunter2" not in response.text and "evil" not in response.text


@pytest.mark.parametrize("value, expected", [
    ("http://localhost:8001/", "http://localhost:8001"),
    ("http://localhost:8001", "http://localhost:8001"),
    ("  http://localhost:8001//  ", "http://localhost:8001"),
    ("https://crm.example.org/backend/", "https://crm.example.org/backend"),
])
def test_trailing_slashes_are_normalized(monkeypatch, value, expected):
    monkeypatch.setenv("CRM_API_URL", value)
    assert crm_api.crm_base_url() == expected


@pytest.mark.parametrize("base", ["http://crm.test:8001/", "http://crm.test:8001", "http://crm.test:8001/prefix/"])
def test_request_goes_to_the_configured_origin_and_path(client, crm, monkeypatch, base):
    monkeypatch.setenv("CRM_API_URL", base)
    crm.answers.append(httpx.Response(200, json=NEEDS_INFORMATION))
    propose(client, {"first_name": "Dana"})
    sent = crm.requests[0].url
    prefix = "/prefix" if "prefix" in base else ""
    assert (sent.scheme, sent.host, sent.port) == ("http", "crm.test", 8001)
    assert sent.path == f"{prefix}/api/assistant/proposals"


def test_real_client_never_follows_redirects():
    client = REAL_MAKE_CRM_CLIENT("http://crm.test:8001")  # built only, nothing sent
    try:
        assert client.follow_redirects is False
    finally:
        client.close()


# --- the user's token ----------------------------------------------------------

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic dXNlcjpwYXNz"}, {"Authorization": "Bearer "}])
def test_missing_token_is_refused_before_calling_the_crm(client, crm, headers):
    response = propose(client, {"first_name": "Dana"}, headers=headers)
    assert response.status_code == 401
    assert response.json()["status"] == "unauthorized"
    assert confirm(client, headers=headers).status_code == 401
    assert crm.requests == []


def test_token_is_forwarded_only_as_the_bearer_header(client, crm):
    crm.answers += [httpx.Response(201, json=PROPOSAL), httpx.Response(201, json=created(False))]
    propose(client, {"first_name": "Dana", "last_name": "Levi"})
    confirm(client)
    for request in crm.requests:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in request.content.decode()
        assert TOKEN not in str(request.url)


def test_redirect_is_not_followed_and_the_token_goes_nowhere_else(client, crm):
    redirect = httpx.Response(307, headers={"Location": "http://evil.test/steal"})
    crm.answers += [redirect, httpx.Response(308, headers={"Location": "http://evil.test/steal"})]
    response = propose(client, {"first_name": "Dana"})
    assert response.status_code == 502
    assert response.json()["status"] == "crm_redirect_refused"
    confirmed = confirm(client)
    assert confirmed.json()["status"] == "crm_redirect_refused"
    assert [request.url.host for request in crm.requests] == ["crm.test", "crm.test"]


def test_token_never_appears_in_logs_or_answers(client, crm, caplog):
    caplog.set_level(logging.DEBUG)
    crm.answers += [
        httpx.Response(201, json=PROPOSAL),
        httpx.ReadTimeout("timed out"),
        httpx.Response(401, json={"detail": "Could not validate credentials"}),
        httpx.Response(500, text="Internal Server Error"),
    ]
    answers = [propose(client, {"first_name": "Dana", "last_name": "Levi"}), confirm(client), confirm(client),
               confirm(client)]
    assert caplog.records  # the bridge does log outcomes
    assert TOKEN not in caplog.text
    assert "secret-user-token" not in caplog.text
    for answer in answers:
        assert TOKEN not in answer.text


def test_identity_fields_in_the_body_are_refused(client, crm):
    response = propose(client, {"first_name": "Dana", "organization_id": 7})
    assert response.status_code == 422
    response = client.post("/crm/contact-drafts/proposals",
                           json={"fields": {"first_name": "Dana"}, "user_id": 3}, headers=AUTH)
    assert response.status_code == 422
    response = confirm(client, body={"payload_hash": PAYLOAD_HASH, "organization_id": 7})
    assert response.status_code == 422
    assert crm.requests == []


# --- proposals -----------------------------------------------------------------

def test_missing_fields_return_needs_information_with_the_normalized_fields(client, crm):
    crm.answers.append(httpx.Response(200, json=NEEDS_INFORMATION))
    response = propose(client, {"first_name": " Dana ", "phone": "052-123-4567"})
    assert response.status_code == 200
    assert response.json() == {
        "status": "needs_information",
        "missing_fields": ["last_name"],
        "invalid_fields": [],
        "fields": NEEDS_INFORMATION["fields"],
    }
    assert crm.requests[0].url.path == "/api/assistant/proposals"
    assert json.loads(crm.requests[0].content) == {
        "action": "create_contact_draft",
        "fields": {"first_name": " Dana ", "last_name": None, "phone": "052-123-4567", "email": None,
                   "address": None},
    }


def test_resubmitting_completed_details_needs_no_shared_state(client, crm):
    """The caller sends the returned fields back with the missing one filled in."""
    crm.answers += [httpx.Response(200, json=NEEDS_INFORMATION), httpx.Response(201, json=PROPOSAL)]
    first = propose(client, {"first_name": "Dana", "phone": "0521234567"}).json()
    completed = {**first["fields"], "last_name": "Levi"}
    second = propose(client, completed)
    assert second.status_code == 201
    assert json.loads(crm.requests[1].content)["fields"] == {**NEEDS_INFORMATION["fields"], "last_name": "Levi"}


def test_valid_details_return_the_exact_proposal_and_nothing_is_confirmed(client, crm):
    crm.answers.append(httpx.Response(201, json=PROPOSAL))
    response = propose(client, {"first_name": "Dana", "last_name": "Levi", "phone": "0521234567"})
    assert response.status_code == 201
    assert response.json() == {
        "status": "proposal",
        "proposal_id": PROPOSAL_ID,
        "payload_hash": PAYLOAD_HASH,
        "expires_at": "2026-10-25T12:15:00Z",
        "draft": PROPOSAL["draft"],
    }
    assert len(crm.requests) == 1  # no automatic confirmation
    assert not crm.requests[0].url.path.endswith("/confirm")


def test_invalid_details_are_reported_per_field(client, crm):
    detail = {"status": "invalid", "invalid_fields": [{"field": "phone", "code": "invalid_phone"}],
              "missing_fields": []}
    crm.answers.append(httpx.Response(422, json={"detail": detail}))
    response = propose(client, {"first_name": "Dana", "last_name": "Levi", "phone": "123"})
    assert response.status_code == 422
    assert response.json() == detail


def test_crm_schema_rejection_is_a_contract_error(client, crm):
    crm.answers.append(httpx.Response(422, json={"detail": [{"type": "extra_forbidden", "loc": ["body", "x"]}]}))
    response = propose(client, {"first_name": "Dana"})
    assert response.status_code == 502
    assert response.json()["status"] == "crm_contract_error"


@pytest.mark.parametrize("answer, http_status, status", [
    (httpx.Response(401, json={"detail": "Could not validate credentials"}), 401, "unauthorized"),
    (httpx.Response(403, json={"detail": "Campaign managers cannot access this resource"}), 403, "forbidden"),
    (httpx.Response(500, text="boom"), 502, "crm_error"),
    (httpx.Response(200, json={"status": "something new"}), 502, "crm_error"),
    (httpx.Response(200, text="<html>not json</html>"), 502, "crm_error"),
])
def test_proposal_failures_are_mapped_honestly(client, crm, answer, http_status, status):
    crm.answers.append(answer)
    response = propose(client, {"first_name": "Dana", "last_name": "Levi"})
    assert response.status_code == http_status
    assert response.json()["status"] == status


def test_forbidden_keeps_the_crm_reason(client, crm):
    crm.answers.append(httpx.Response(403, json={"detail": "Campaign managers cannot access this resource"}))
    assert propose(client, {"first_name": "Dana"}).json()["message"] == "Campaign managers cannot access this resource"


@pytest.mark.parametrize("error, status", [
    (httpx.ReadTimeout("timed out"), "crm_timeout"),
    (httpx.ConnectError("refused"), "crm_unreachable"),
])
def test_proposal_timeout_is_reported_and_not_retried_automatically(client, crm, error, status):
    crm.answers.append(error)
    response = propose(client, {"first_name": "Dana", "last_name": "Levi"})
    assert response.json()["status"] == status
    assert response.json()["retry_safe"] is True
    assert len(crm.requests) == 1


# --- confirmation --------------------------------------------------------------

def test_confirm_returns_the_saved_draft_from_the_crm(client, crm):
    crm.answers.append(httpx.Response(201, json=created(False)))
    response = confirm(client, body={"payload_hash": PAYLOAD_HASH, "fields": DRAFT_FIELDS})
    assert response.status_code == 201
    assert response.json() == {key: created(False)[key]
                               for key in ("status", "replayed", "proposal_id", "contact_id", "contact")}
    request = crm.requests[0]
    assert request.url.path == f"/api/assistant/proposals/{PROPOSAL_ID}/confirm"
    assert json.loads(request.content) == {"payload_hash": PAYLOAD_HASH, "fields": DRAFT_FIELDS}


def test_confirm_without_fields_sends_only_the_hash(client, crm):
    crm.answers.append(httpx.Response(201, json=created(False)))
    confirm(client)
    assert json.loads(crm.requests[0].content) == {"payload_hash": PAYLOAD_HASH}


def test_retry_returns_the_original_replayed_result(client, crm):
    crm.answers += [httpx.Response(201, json=created(False)), httpx.Response(200, json=created(True))]
    first = confirm(client).json()
    retry = confirm(client)
    assert retry.status_code == 200
    assert retry.json()["replayed"] is True
    assert retry.json()["contact_id"] == first["contact_id"]


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("timed out"),
    httpx.WriteTimeout("timed out"),
    httpx.RemoteProtocolError("server disconnected"),
    httpx.ReadError("connection reset"),
])
def test_lost_answer_is_uncertain_and_keeps_the_same_proposal(client, crm, error):
    crm.answers += [error, httpx.Response(200, json=created(True))]
    response = confirm(client)
    assert response.status_code == 504
    body = response.json()
    assert body["status"] == "uncertain"
    assert "not known whether the draft was saved" in body["message"]
    assert (body["proposal_id"], body["payload_hash"], body["retry_safe"]) == (PROPOSAL_ID, PAYLOAD_HASH, True)
    assert len(crm.requests) == 1  # no automatic retry, no new proposal

    retry = confirm(client, body={"payload_hash": body["payload_hash"]}, proposal_id=body["proposal_id"])
    assert retry.json()["status"] == "created" and retry.json()["replayed"] is True
    assert [r.url.path for r in crm.requests] == [f"/api/assistant/proposals/{PROPOSAL_ID}/confirm"] * 2


@pytest.mark.parametrize("answer", [
    httpx.Response(500, text="Internal Server Error"),
    httpx.Response(502, text="Bad Gateway"),
    httpx.Response(201, text="not json"),
    httpx.Response(200, json={"status": "pending"}),
])
def test_crm_errors_and_unreadable_success_are_uncertain_not_failed(client, crm, answer):
    crm.answers.append(answer)
    response = confirm(client)
    assert response.status_code == 502
    assert response.json()["status"] == "uncertain"
    assert response.json()["proposal_id"] == PROPOSAL_ID


def test_no_connection_means_not_sent(client, crm):
    crm.answers.append(httpx.ConnectError("connection refused"))
    response = confirm(client)
    assert response.json()["status"] == "crm_unreachable"
    assert response.json()["proposal_id"] == PROPOSAL_ID


@pytest.mark.parametrize("answer, http_status, status", [
    (httpx.Response(409, json={"detail": "The confirmed details do not match this proposal"}), 409,
     "payload_mismatch"),
    (httpx.Response(409, json={"detail": "The proposal could not be confirmed — try again"}), 409, "conflict"),
    (httpx.Response(404, json={"detail": "Proposal not found"}), 404, "unknown_proposal"),
    (httpx.Response(410, json={"detail": "Proposal has expired — create a new one"}), 410, "expired"),
    (httpx.Response(401, json={"detail": "Could not validate credentials"}), 401, "unauthorized"),
    (httpx.Response(403, json={"detail": "Campaign managers cannot access this resource"}), 403, "forbidden"),
    (httpx.Response(422, json={"detail": {"status": "invalid", "invalid_fields": [
        {"field": "phone", "code": "phone_taken"}], "missing_fields": []}}), 422, "invalid"),
])
def test_confirm_failures_are_mapped_honestly(client, crm, answer, http_status, status):
    crm.answers.append(answer)
    response = confirm(client)
    assert response.status_code == http_status
    assert response.json()["status"] == status
    assert "created" not in response.text


def test_conflict_that_can_be_retried_keeps_the_proposal(client, crm):
    crm.answers.append(httpx.Response(409, json={"detail": "The proposal could not be confirmed — try again"}))
    body = confirm(client).json()
    assert (body["proposal_id"], body["retry_safe"]) == (PROPOSAL_ID, True)


@pytest.mark.parametrize("proposal_id", ["not-a-uuid", "..", "0B6F2C1E-8A4D-4C1B-9E2F-3A5D7C9E1F20x"])
def test_malformed_proposal_id_never_reaches_the_crm(client, crm, proposal_id):
    response = confirm(client, proposal_id=proposal_id)
    assert response.status_code == 404
    assert crm.requests == []


def test_empty_payload_hash_is_refused(client, crm):
    assert confirm(client, body={"payload_hash": ""}).status_code == 422
    assert crm.requests == []
