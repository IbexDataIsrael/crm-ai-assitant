"""Login proxy for the chat page: POST /auth/login -> the CRM's POST /api/auth/token.

The CRM is always a mock transport (fake_crm in conftest.py). The answers copy
sheepsidebay_crm backend/app/routers/auth.py (LoginResponse, auth_error).
"""

import logging
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "Correct-Horse-9-secret"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.login-token-secret.sig"


@pytest.fixture
def client():
    return TestClient(app)  # no lifespan: the vector store isn't needed


def log_in(client, **body):
    return client.post("/auth/login", json={"username": "dana@example.org", "password": PASSWORD, **body})


def form_of(request):
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


def test_success_returns_the_token_only_to_the_page(client, fake_crm, caplog):
    caplog.set_level(logging.DEBUG)
    fake_crm.answers.append(httpx.Response(200, json={"access_token": TOKEN, "token_type": "bearer",
                                                      "requires_org_selection": False, "organizations": []}))
    response = log_in(client)
    assert response.status_code == 200
    assert response.json() == {"status": "logged_in", "access_token": TOKEN, "language": None}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers  # the token is not put in a cookie
    request = fake_crm.requests[0]
    assert request.url.host == "crm.test" and request.url.path == "/api/auth/token"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert form_of(request) == {"username": "dana@example.org", "password": PASSWORD}
    assert PASSWORD not in caplog.text and TOKEN not in caplog.text and "dana@example.org" not in caplog.text


def test_wrong_credentials_are_reported_without_echoing_them(client, fake_crm):
    fake_crm.answers.append(httpx.Response(401, json={"detail": {
        "code": "auth.error.invalid_credentials", "message": "Incorrect username or password"}}))
    response = log_in(client)
    assert response.status_code == 401
    assert response.json()["status"] == "invalid_credentials"
    assert PASSWORD not in response.text


def test_several_organizations_ask_for_a_choice_then_log_in_with_it(client, fake_crm):
    fake_crm.answers += [
        httpx.Response(200, json={"access_token": None, "token_type": "bearer", "requires_org_selection": True,
                                  "organizations": [{"id": 1, "name": "North", "logo_url": None},
                                                    {"id": 2, "name": "South", "logo_url": None}]}),
        httpx.Response(200, json={"access_token": TOKEN, "token_type": "bearer"}),
    ]
    first = log_in(client)
    assert first.status_code == 200
    assert first.json() == {"status": "choose_organization",
                            "organizations": [{"id": 1, "name": "North"}, {"id": 2, "name": "South"}]}
    assert "organization_id" not in form_of(fake_crm.requests[0])

    second = log_in(client, organization_id=2)
    assert second.json() == {"status": "logged_in", "access_token": TOKEN, "language": None}
    assert form_of(fake_crm.requests[1])["organization_id"] == "2"


@pytest.mark.parametrize("code, status", [
    ("auth.error.not_verified", "not_verified"),
    ("auth.error.org_pending", "org_pending"),
    ("auth.error.org_disabled", "org_disabled"),
    ("something.new", "login_refused"),
])
def test_refusals_keep_the_crm_reason_code(client, fake_crm, code, status):
    fake_crm.answers.append(httpx.Response(403, json={"detail": {"code": code, "message": "x"}}))
    response = log_in(client)
    assert response.status_code == 403
    assert response.json()["status"] == status


def test_redirect_is_never_followed_with_the_password(client, fake_crm):
    fake_crm.answers.append(httpx.Response(307, headers={"Location": "http://evil.test/api/auth/token"}))
    response = log_in(client)
    assert response.status_code == 502
    assert response.json()["status"] == "crm_redirect_refused"
    assert [r.url.host for r in fake_crm.requests] == ["crm.test"]


@pytest.mark.parametrize("body", [
    {"password": PASSWORD},
    {"username": "dana", "password": ""},
    {"username": "dana", "password": PASSWORD, "remember": True},
    {"username": "dana", "password": PASSWORD, "organization_id": "two"},
    [PASSWORD],
])
def test_malformed_logins_are_refused_without_echo_or_crm_call(client, fake_crm, body):
    response = client.post("/auth/login", json=body)
    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert fake_crm.requests == []


def test_unreachable_or_unconfigured_crm(client, fake_crm, monkeypatch):
    fake_crm.answers.append(httpx.ConnectError("refused"))
    assert log_in(client).json()["status"] == "crm_unreachable"
    monkeypatch.delenv("CRM_API_URL")
    response = log_in(client)
    assert response.status_code == 503
    assert response.json()["status"] == "crm_not_configured"
