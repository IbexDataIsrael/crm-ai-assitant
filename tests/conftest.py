"""Shared test setup: tests never see a real key and can never call Gemini,
and they use their own database, never the app's rows."""

import os
from pathlib import Path

import psycopg
import pytest

from app import generation, knowledge
from app.embeddings import get_embedder
from app.vectorstore import VectorStore, database_url

# The fictional sample documents, kept as fixed test data (no longer searched by the app).
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_DOCUMENTS = ("donations.md", "sample.md", "visits.md")
TEST_DATABASE = "crm_assistant_test"


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Point every store at a separate test database on the same Postgres server.

    It is created on first use and kept, so later runs reuse its vectors.
    """
    app_url = database_url()
    with psycopg.connect(app_url, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE,)).fetchone():
            conn.execute(f"CREATE DATABASE {TEST_DATABASE}")
    test_url = app_url.rsplit("/", 1)[0] + "/" + TEST_DATABASE
    os.environ["DATABASE_URL"] = test_url
    knowledge.get_store.cache_clear()
    yield test_url
    os.environ["DATABASE_URL"] = app_url


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


@pytest.fixture(autouse=True)
def no_live_api_calls(monkeypatch):
    for variable in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_THINKING_LEVEL",
                     "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANSWER_PROVIDER"):
        monkeypatch.delenv(variable, raising=False)

    def refuse(api_key):
        raise AssertionError("A test tried to create a real API client")

    monkeypatch.setattr(generation, "make_client", refuse)
    monkeypatch.setattr(generation, "make_anthropic_client", refuse)


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
