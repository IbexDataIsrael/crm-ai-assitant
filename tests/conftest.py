"""Shared test setup: tests never see a real key and can never call Gemini,
and they use their own database and login role, never the app's."""

import json
import os
from pathlib import Path

import httpx
import pytest

from app import crm_api, generation, knowledge, rate_limit
from app.embeddings import get_embedder
from app.vectorstore import VectorStore, database_dsn

# The fictional sample documents, kept as fixed test data (no longer searched by the app).
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_DOCUMENTS = ("donations.md", "sample.md", "visits.md")


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Point every store at the test database, logged in as the test role (crm_test).

    The database, its table and the role come from scripts/db/setup-roles.sql;
    crm_test can neither create databases nor reach the app's database.
    """
    test_dsn = database_dsn("TEST")
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_dsn
    knowledge.get_store.cache_clear()
    yield test_dsn
    if previous is None:
        del os.environ["DATABASE_URL"]
    else:
        os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def sample_store(test_database):
    """A vector store built from tests/fixtures only, with the real local model."""
    store = VectorStore(get_embedder(), collection="test_fixtures")
    store.build(FIXTURES_DIR, filenames=SAMPLE_DOCUMENTS)
    return store


@pytest.fixture
def sample_knowledge(monkeypatch, sample_store):
    """Make knowledge.search() use the fictional sample store instead of the user guides."""
    monkeypatch.setattr(knowledge, "get_store", lambda: sample_store)
    return sample_store

# The real factories, kept before the fixture below replaces them. Only used
# to check client settings offline - never to send a request.
REAL_MAKE_CLIENT = generation.make_client
REAL_MAKE_ANTHROPIC_CLIENT = generation.make_anthropic_client
REAL_MAKE_CRM_CLIENT = crm_api.make_http_client


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Each test starts with empty request counts (all tests share one client address)."""
    rate_limit.limiter.reset()
    yield
    rate_limit.limiter.reset()


@pytest.fixture(autouse=True)
def no_live_api_calls(monkeypatch):
    for variable in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_THINKING_LEVEL",
                     "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANSWER_PROVIDER"):
        monkeypatch.delenv(variable, raising=False)

    def refuse(api_key):
        raise AssertionError("A test tried to create a real API client")

    monkeypatch.setattr(generation, "make_client", refuse)
    monkeypatch.setattr(generation, "make_anthropic_client", refuse)

    # No real CRM either: tests that need it install a mock transport.
    monkeypatch.delenv("CRM_API_URL", raising=False)

    def refuse_crm(base_url):
        raise AssertionError("A test tried to create a real CRM HTTP client")

    monkeypatch.setattr(crm_api, "make_http_client", refuse_crm)


class FakeInteraction:
    def __init__(self, output_text, status="completed"):
        self.output_text = output_text
        self.status = status


class FakeGemini:
    """Stands in for genai.Client: records the request, returns or raises what it was given."""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.requests = []
        self.interactions = self

    def create(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def fake_gemini(monkeypatch):
    """Install a fake client with a fake key. Set .reply or .error in the test."""
    fake = FakeGemini()
    monkeypatch.setenv("GEMINI_API_KEY", "test-placeholder-not-a-key")
    monkeypatch.setattr(generation, "make_client", lambda api_key: fake)
    return fake


class FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeMessage:
    def __init__(self, text, stop_reason="end_turn", input_tokens=400, output_tokens=60):
        self.content = [FakeTextBlock(text)] if text is not None else []
        self.stop_reason = stop_reason
        self.usage = FakeUsage(input_tokens, output_tokens)


class FakeTokenCount:
    def __init__(self, input_tokens):
        self.input_tokens = input_tokens


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: records count_tokens and create calls."""

    def __init__(self):
        self.counted_tokens = 400
        self.count_error = None
        self.reply = None
        self.error = None
        self.counts = []
        self.creates = []
        self.messages = self

    def count_tokens(self, **request):
        self.counts.append(request)
        if self.count_error:
            raise self.count_error
        return FakeTokenCount(self.counted_tokens)

    def create(self, **request):
        self.creates.append(request)
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Select the Anthropic provider with a fake key and a fake client."""
    fake = FakeAnthropic()
    monkeypatch.setenv("ANSWER_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-placeholder-not-a-key")
    monkeypatch.setattr(generation, "make_anthropic_client", lambda api_key: fake)
    return fake


# A user as the CRM's GET /api/auth/me returns it (UserOut, the fields checked).
CRM_ME = {"id": 7, "guid": "u-7", "username": "dana", "full_name": "Dana Levi", "role": "staff",
          "is_active": True, "organization_id": 3, "created_at": "2026-01-01T00:00:00"}


class FakeCRM:
    """A mock CRM: answers each request with the next item (an httpx.Response, or an exception to raise).

    The login check (GET /api/auth/me) is answered separately: from me_answers
    if a test queued any, otherwise with a valid user. Those calls are recorded
    in me_requests, so `requests` keeps only the bridge and login calls.
    """

    def __init__(self):
        self.answers = []
        self.requests = []
        self.me_answers = []
        self.me_requests = []
        # GET/PUT /api/preferences (the user's language): answered from pref_answers if a
        # test queued any, otherwise GET -> pref_map and PUT -> stored and echoed.
        self.pref_map = {}
        self.pref_answers = []
        self.pref_requests = []

    def puts(self):
        """The language saves sent, as their JSON bodies."""
        return [json.loads(r.content) for r in self.pref_requests if r.method == "PUT"]

    def handler(self, request):
        if request.url.path == crm_api.PREFERENCES_PATH:
            self.pref_requests.append(request)
            if self.pref_answers:
                answer = self.pref_answers.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer
            if request.method == "PUT":
                body = json.loads(request.content)
                self.pref_map[body["key"]] = body["value"]
            return httpx.Response(200, json=self.pref_map)
        if request.url.path == crm_api.ME_PATH:
            self.me_requests.append(request)
            if not self.me_answers:
                return httpx.Response(200, json=CRM_ME)
            answer = self.me_answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def paths(self):
        return [request.url.path for request in self.requests]


@pytest.fixture
def fake_crm(monkeypatch):
    """Set CRM_API_URL and route every CRM call (bridge, login, login check) to a FakeCRM."""
    monkeypatch.setenv("CRM_API_URL", "http://crm.test:8001/")
    fake = FakeCRM()
    monkeypatch.setattr(crm_api, "make_http_client", lambda base_url: httpx.Client(
        base_url=base_url, transport=httpx.MockTransport(fake.handler), follow_redirects=False))
    return fake


# A token the fake CRM accepts at GET /api/auth/me (the value itself is never checked here).
TEST_TOKEN = "eyJhbGciOiJIUzI1NiJ9.test-user-secret.sig"
TEST_AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def logged_in(fake_crm):
    """The chat routes need a login the CRM confirms: the fake CRM confirms TEST_TOKEN."""
    return TEST_AUTH
