"""Shared test setup: tests never see a real key and can never call Gemini."""

import pytest

from app import generation

# The real factory, kept before the fixture below replaces it. Only used to
# inspect client settings offline - never to send a request.
REAL_MAKE_CLIENT = generation.make_client


@pytest.fixture(autouse=True)
def no_live_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    def refuse(api_key):
        raise AssertionError("A test tried to create a real Gemini client")

    monkeypatch.setattr(generation, "make_client", refuse)


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
