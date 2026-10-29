"""Login enforcement and rate limits (docs/instructions17.md).

Every CRM call is a mock (fake_crm in conftest.py); every AI provider is
mocked or refused. Shows: no token -> 401 and no AI work; a token the CRM
confirms works; a CRM outage answers "temporarily unavailable" and keeps the
session; repeated logins are refused; a spoofed X-Forwarded-For header does
not change which client is counted.
"""

import logging
import os
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from app import knowledge, rate_limit
from app.main import app
from tests.conftest import CRM_ME, TEST_AUTH, TEST_TOKEN, FakeInteraction

QUESTION = "How do I deal with money a supporter gives me?"
OPEN_ROUTES = {"/", "/health", "/auth/login"}
# FastAPI's own documentation routes (off unless API_DOCS=on).
DOCS_ROUTES = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


@pytest.fixture
def client():
    return TestClient(app)  # no lifespan: the knowledge tests use sample_knowledge


@pytest.fixture
def no_search(monkeypatch):
    """Fails the test if a refused request still reaches guide retrieval."""
    def refuse(*args, **kwargs):
        raise AssertionError("retrieval ran for a refused request")

    monkeypatch.setattr(knowledge, "search", refuse)


def ask(client, headers=TEST_AUTH, message=QUESTION):
    return client.post("/chat", json={"message": message}, headers=headers)


def turn(client, intent="ask_question", message=QUESTION, headers=TEST_AUTH):
    return client.post("/assistant/turn", json={"intent": intent, "message": message}, headers=headers)


# --- which routes are open --------------------------------------------------------

def test_every_route_except_page_health_and_login_needs_a_token(client, fake_crm, no_search):
    bodies = {
        "/chat": {"message": QUESTION},
        "/assistant/turn": {"intent": "ask_question", "message": QUESTION},
        "/assistant/language": {"language": "he"},
        "/crm/contact-drafts/proposals": {"fields": {"first_name": "Dana"}},
        "/crm/contact-drafts/proposals/{proposal_id}/confirm": {"payload_hash": "a" * 64},
    }
    checked = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path in OPEN_ROUTES or path in DOCS_ROUTES:
            continue
        assert path in bodies, f"new route {path}: decide whether it needs a login, then add it here"
        url = path.replace("{proposal_id}", "0b6f2c1e-8a4d-4c1b-9e2f-3a5d7c9e1f20")
        response = client.post(url, json=bodies[path])
        assert response.status_code == 401, path
        checked.add(path)
    assert checked == set(bodies)
    assert fake_crm.requests == [] and fake_crm.me_requests == [] and fake_crm.pref_requests == []


@pytest.mark.parametrize("intent", ["create_contact", "update_contact", "edit_apartment", "ask_question"])
def test_every_turn_intent_needs_a_token(client, fake_crm, fake_gemini, no_search, intent):
    response = turn(client, intent, headers={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    data = response.json()
    assert data["status"] == "unauthorized" and data["logout"] is True and data["intent"] == intent
    assert fake_gemini.requests == [] and fake_crm.me_requests == [] and fake_crm.requests == []


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer "}, {"Authorization": "Basic ZGFuYTpwdw=="},
                                     {"Authorization": TEST_TOKEN}])
def test_chat_without_a_bearer_token_is_401_and_does_no_ai_work(client, fake_crm, fake_gemini, no_search, headers):
    response = ask(client, headers)
    assert response.status_code == 401
    assert response.json()["logout"] is True
    assert fake_gemini.requests == [] and fake_crm.me_requests == []


def test_api_docs_are_off_unless_api_docs_is_on():
    # A fresh interpreter, so app.main is built with the given environment.
    script = "from app.main import app; print(sorted(r.path for r in app.routes))"
    for value, expect_docs in (("", False), ("off", False), ("yes", False), ("on", True)):
        env = {**os.environ, "API_DOCS": value}
        out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True,
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), check=True).stdout
        assert ("'/openapi.json'" in out) is expect_docs, value
        assert ("'/docs'" in out) is expect_docs, value


# --- a valid login -------------------------------------------------------------------

def test_a_token_the_crm_confirms_gets_an_answer(client, fake_crm, sample_knowledge, fake_gemini):
    fake_gemini.reply = FakeInteraction("Count it with a second volunteer [1].")
    data = ask(client).json()
    assert data["generation"]["status"] == "answered"
    [check] = fake_crm.me_requests
    assert check.method == "GET"
    assert str(check.url) == "http://crm.test:8001/api/auth/me"
    assert check.headers["authorization"] == f"Bearer {TEST_TOKEN}"
    assert check.content == b""


def test_every_turn_checks_the_login_again_no_cache(client, fake_crm, sample_knowledge, fake_gemini):
    fake_gemini.reply = FakeInteraction("Count it with a second volunteer [1].")
    for _ in range(3):
        assert turn(client).status_code == 200
    assert len(fake_crm.me_requests) == 3


def test_update_choice_also_needs_the_crm_check(client, fake_crm):
    fake_crm.me_answers.append(httpx.Response(401, json={"detail": "Could not validate credentials"}))
    assert turn(client, "update_contact", "").status_code == 401
    assert turn(client, "update_contact", "").json()["outcome"] == "not_available"


def test_create_contact_checks_the_login_and_then_forwards_the_same_token(client, fake_crm):
    data = turn(client, "create_contact", "").json()
    assert data["outcome"] == "started"
    assert len(fake_crm.me_requests) == 1 and fake_crm.requests == []


# --- the CRM refuses -------------------------------------------------------------------

def test_crm_401_means_log_in_again(client, fake_crm, fake_gemini, no_search):
    fake_crm.me_answers.append(httpx.Response(401, json={"detail": "Could not validate credentials"}))
    response = ask(client)
    assert response.status_code == 401
    assert response.json()["logout"] is True and response.json()["status"] == "unauthorized"
    assert fake_gemini.requests == []


def test_crm_403_is_a_permission_problem_not_a_logout(client, fake_crm, fake_gemini, no_search):
    fake_crm.me_answers.append(httpx.Response(403, json={"detail": "Forbidden"}))
    response = ask(client)
    assert response.status_code == 403
    assert response.json()["logout"] is False and response.json()["status"] == "forbidden"
    assert fake_gemini.requests == []


# --- the CRM is down or answers something unexpected: fail closed, keep the session ----

OUTAGES = {
    "timeout": httpx.ReadTimeout("slow"),
    "connect timeout": httpx.ConnectTimeout("slow"),
    "unreachable": httpx.ConnectError("refused"),
    "dropped": httpx.RemoteProtocolError("dropped"),
    "500": httpx.Response(500, text="Internal Server Error"),
    "502": httpx.Response(502, text="Bad Gateway"),
    "503": httpx.Response(503),
    "429": httpx.Response(429),
    "404": httpx.Response(404, json={"detail": "Not Found"}),
    "redirect": httpx.Response(302, headers={"Location": "http://elsewhere.test/api/auth/me"}),
    "200 not json": httpx.Response(200, text="<html>ok</html>"),
    "200 empty": httpx.Response(200),
    "200 list": httpx.Response(200, json=[CRM_ME]),
    "200 no id": httpx.Response(200, json={k: v for k, v in CRM_ME.items() if k != "id"}),
    "200 no organization": httpx.Response(200, json={k: v for k, v in CRM_ME.items() if k != "organization_id"}),
    "200 id is text": httpx.Response(200, json={**CRM_ME, "id": "7"}),
    "200 inactive": httpx.Response(200, json={**CRM_ME, "is_active": False}),
    "201": httpx.Response(201, json=CRM_ME),
}


@pytest.mark.parametrize("answer", OUTAGES.values(), ids=OUTAGES.keys())
def test_crm_outage_or_odd_answer_is_temporarily_unavailable(client, fake_crm, fake_gemini, no_search, answer):
    fake_crm.me_answers.append(answer)
    response = turn(client)
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unavailable"
    assert "temporarily unavailable" in data["reply"] and "still logged in" in data["reply"]
    # Never shown as a logout or a wrong password.
    assert data["logout"] is False
    assert "password" not in data["reply"].lower() and "log in again" not in data["reply"].lower()
    assert fake_gemini.requests == []
    # One request only: the redirect is not followed.
    assert len(fake_crm.me_requests) == 1


def test_crm_not_configured_is_unavailable_too(client, fake_crm, monkeypatch, no_search):
    monkeypatch.delenv("CRM_API_URL")
    response = ask(client)
    assert response.status_code == 503 and response.json()["logout"] is False
    assert fake_crm.me_requests == []


def test_hebrew_refusals_are_in_hebrew(client, fake_crm):
    fake_crm.me_answers.append(httpx.ReadTimeout("slow"))
    response = client.post("/assistant/turn", json={"intent": "ask_question", "message": "שלום", "language": "he"},
                           headers=TEST_AUTH)
    assert response.status_code == 503
    assert response.json()["reply"].startswith("השירות אינו זמין כרגע")


def test_the_token_is_never_logged_or_returned(client, fake_crm, caplog):
    caplog.set_level(logging.DEBUG)
    fake_crm.me_answers += [httpx.Response(401), httpx.ReadTimeout("slow"), httpx.Response(200, text="x")]
    for _ in range(3):
        response = ask(client)
        assert "test-user-secret" not in response.text
    assert "test-user-secret" not in caplog.text


# --- rate limits -------------------------------------------------------------------------

def logins(client, count, headers=None):
    return [client.post("/auth/login", json={"username": "dana", "password": "wrong-password"},
                        headers=headers or {}) for _ in range(count)]


def test_repeated_login_attempts_are_refused(client, fake_crm):
    limit = rate_limit.LIMITS["login"].requests
    fake_crm.answers += [httpx.Response(401, json={"detail": "Incorrect username or password"})] * limit
    answers = logins(client, limit + 2)
    assert [a.status_code for a in answers[:limit]] == [401] * limit
    for refused in answers[limit:]:
        assert refused.status_code == 429
        assert refused.json()["status"] == "rate_limited"
        assert int(refused.headers["retry-after"]) > 0
        assert refused.headers.get("set-cookie") is None
    # The refused attempts never reached the CRM.
    assert len(fake_crm.requests) == limit


def test_chat_turns_are_rate_limited_before_the_crm_check(client, fake_crm, monkeypatch):
    monkeypatch.setitem(rate_limit.LIMITS, "chat", rate_limit.Limit(3, 60.0))
    answers = [turn(client, "update_contact", "") for _ in range(5)]
    assert [a.status_code for a in answers] == [200, 200, 200, 429, 429]
    assert answers[-1].json()["logout"] is False
    assert len(fake_crm.me_requests) == 3


def test_a_spoofed_forwarded_for_header_does_not_change_the_client(client, fake_crm):
    # No proxy is trusted here, so the header is ignored: one client, one count.
    limit = rate_limit.LIMITS["login"].requests
    fake_crm.answers += [httpx.Response(401)] * limit
    for i in range(limit):
        assert logins(client, 1, {"X-Forwarded-For": f"203.0.113.{i}"})[0].status_code == 401
    assert logins(client, 1, {"X-Forwarded-For": "198.51.100.99"})[0].status_code == 429
    assert logins(client, 1)[0].status_code == 429


PROXY = ("10.0.0.2", 40000)


@pytest.fixture
def behind_proxy(monkeypatch):
    """The app sees every request from the reverse proxy at 10.0.0.2."""
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", rate_limit.parse_trusted_proxies("10.0.0.2"))
    return TestClient(app, client=PROXY)


def test_behind_the_trusted_proxy_each_user_is_counted_separately(behind_proxy, fake_crm, monkeypatch):
    monkeypatch.setitem(rate_limit.LIMITS, "login", rate_limit.Limit(2, 600.0))
    fake_crm.answers += [httpx.Response(401)] * 4
    alice, bob = {"X-Forwarded-For": "198.51.100.1"}, {"X-Forwarded-For": "198.51.100.2"}
    assert [a.status_code for a in logins(behind_proxy, 3, alice)] == [401, 401, 429]
    # Bob is not blocked by Alice: the proxy's address is not "the user".
    assert [a.status_code for a in logins(behind_proxy, 2, bob)] == [401, 401]


def test_behind_the_proxy_a_client_supplied_entry_cannot_reset_the_limit(behind_proxy, fake_crm, monkeypatch):
    monkeypatch.setitem(rate_limit.LIMITS, "login", rate_limit.Limit(2, 600.0))
    fake_crm.answers += [httpx.Response(401)] * 2
    # The user sends their own X-Forwarded-For; the proxy appends the real address.
    for fake in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        status = logins(behind_proxy, 1, {"X-Forwarded-For": f"{fake}, 198.51.100.7"})[0].status_code
    assert status == 429


@pytest.mark.parametrize("forwarded", [None, "", "not-an-ip", "198.51.100.7, garbage", "10.0.0.2"])
def test_behind_the_proxy_no_usable_client_address_is_refused(behind_proxy, fake_crm, forwarded):
    headers = {} if forwarded is None else {"X-Forwarded-For": forwarded}
    response = logins(behind_proxy, 1, headers)[0]
    assert response.status_code == 400
    assert response.json()["status"] == "bad_client_address"
    assert fake_crm.requests == []


def test_limiter_window_expires_and_a_full_table_fails_closed():
    now = [1000.0]
    limiter = rate_limit.RateLimiter({"g": rate_limit.Limit(2, 60.0)}, max_clients=2, clock=lambda: now[0])
    assert limiter.allow("g", "a")[0] and limiter.allow("g", "a")[0]
    allowed, retry_after = limiter.allow("g", "a")
    assert not allowed and 0 < retry_after <= 61
    assert limiter.allow("g", "b")[0]
    # Table full (a, b): a third client is refused, not let through uncounted.
    assert limiter.allow("g", "c")[0] is False
    now[0] += 61
    # Old entries expire: the table is pruned and everyone may go again.
    assert limiter.allow("g", "c")[0] and limiter.allow("g", "a")[0]


@pytest.mark.parametrize("raw", ["10.0.0.300", "proxy.example", "10.0.0.0/33"])
def test_an_invalid_trusted_proxies_value_is_an_error(raw):
    with pytest.raises(ValueError):
        rate_limit.parse_trusted_proxies(raw)


def test_trusted_proxies_accepts_ips_and_ranges():
    proxies = rate_limit.parse_trusted_proxies(" 172.16.0.0/12, ::1 ,")
    assert len(proxies) == 2
    assert rate_limit.parse_trusted_proxies(None) == () and rate_limit.parse_trusted_proxies("") == ()


def test_the_page_is_always_revalidated(client):
    # An old cached page (token only on create_contact) would be refused on Ask.
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert 'headers["Authorization"] = "Bearer " + token;' in response.text
