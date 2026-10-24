"""/chat endpoint tests: action and clarify placeholders, knowledge retrieval + generation.

Gemini is always mocked here (see conftest.py); no live API calls. Knowledge tests search
the fictional sample documents in tests/fixtures (the sample_knowledge fixture).
"""

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app.knowledge import RETRIEVAL_LABEL
from app.main import app
from tests.conftest import FakeInteraction


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs startup, which builds the vector store
        yield c


def ask(client, message):
    response = client.post("/chat", json={"message": message})
    assert response.status_code == 200
    return response.json()


def test_action_route_still_returns_demo_placeholder(client):
    data = ask(client, "find contact Dana")
    assert data["route"] == "action"
    assert data["route_source"] == "keyword-stub"
    assert "find_contact()" in data["reply"]
    assert "DEMO DATA - not a real CRM record" in data["reply"]
    assert data["passages"] == []
    assert data["generation"] is None


def test_update_action_changes_nothing(client):
    data = ask(client, "update the phone number")
    assert data["route"] == "action"
    assert "No record was changed" in data["reply"]


def test_clarify_route(client):
    data = ask(client, "hello")
    assert data["route"] == "clarify"
    assert data["passages"] == []
    assert data["generation"] is None


@pytest.mark.parametrize(
    "message, filename, heading",
    [
        ("How do I deal with money a supporter gives me?", "donations.md", "Accepting a cash donation"),
        ("How do I pull out of tomorrow's appointment?", "visits.md", "Cancelling a visit"),
    ],
)
def test_knowledge_without_key_shows_passages_only(client, sample_knowledge, message, filename, heading):
    data = ask(client, message)
    assert data["route"] == "knowledge"
    assert data["route_source"] == "keyword-stub"
    assert data["generation"]["status"] == "no_api_key"
    assert data["reply"].startswith("No AI-written answer: answer generation is off because GEMINI_API_KEY is not set.")
    assert RETRIEVAL_LABEL in data["reply"]
    assert len(data["passages"]) == 3
    top = data["passages"][0]
    assert (top["filename"], top["heading"]) == (filename, heading), data["passages"]
    assert f"{filename} > {heading}" in data["reply"]


def test_knowledge_with_mocked_answer(client, sample_knowledge, fake_gemini):
    fake_gemini.reply = FakeInteraction("Count it with a second volunteer and lock it in the office box [1].")
    data = ask(client, "How do I deal with money a supporter gives me?")
    assert data["generation"]["status"] == "answered"
    assert data["reply"].startswith("AI-generated answer (Gemini, gemini-3.8-flash) - based only on the passages below:")
    assert "lock it in the office box [1]" in data["reply"]
    assert "Cited: [1] donations.md > Accepting a cash donation" in data["reply"]
    assert RETRIEVAL_LABEL in data["reply"]
    # Only the question and the three retrieved passages were sent.
    sent = fake_gemini.requests[0]["input"]
    assert sent.startswith("Question: How do I deal with money a supporter gives me?")
    assert sent.count("<passage ") == 3


def test_knowledge_with_mocked_insufficient_sources(client, sample_knowledge, fake_gemini):
    fake_gemini.reply = FakeInteraction("INSUFFICIENT_SOURCES")
    data = ask(client, "How do I reset the office alarm?")
    assert data["generation"]["status"] == "insufficient_sources"
    assert data["reply"].startswith("No answer: Gemini (gemini-3.8-flash) found that the passages below don't answer this question.")


def test_knowledge_with_mocked_api_failure_shows_no_answer(client, sample_knowledge, fake_gemini):
    fake_gemini.error = errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "overloaded"}})
    data = ask(client, "How do I deal with money a supporter gives me?")
    assert data["generation"]["status"] == "error"
    assert data["generation"]["answer"] is None
    assert data["reply"].startswith("No AI-written answer: answer generation failed (Gemini API error 503 UNAVAILABLE: overloaded).")
    assert "AI-generated answer" not in data["reply"]
