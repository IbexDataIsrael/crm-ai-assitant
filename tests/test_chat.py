"""/chat endpoint tests: action and clarify placeholders, knowledge retrieval."""

import pytest
from fastapi.testclient import TestClient

from app.knowledge import RETRIEVAL_LABEL
from app.main import app


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


def test_update_action_changes_nothing(client):
    data = ask(client, "update the phone number")
    assert data["route"] == "action"
    assert "No record was changed" in data["reply"]


def test_clarify_route(client):
    data = ask(client, "hello")
    assert data["route"] == "clarify"
    assert data["passages"] == []


@pytest.mark.parametrize(
    "message, filename, heading",
    [
        ("How do I deal with money a supporter gives me?", "donations.md", "Accepting a cash donation"),
        ("How do I pull out of tomorrow's appointment?", "visits.md", "Cancelling a visit"),
    ],
)
def test_knowledge_route_returns_cited_passages(client, message, filename, heading):
    data = ask(client, message)
    assert data["route"] == "knowledge"
    assert data["route_source"] == "keyword-stub"
    assert data["reply"].startswith(RETRIEVAL_LABEL)
    assert len(data["passages"]) == 3
    top = data["passages"][0]
    assert (top["filename"], top["heading"]) == (filename, heading), data["passages"]
    assert f"{filename} > {heading}" in data["reply"]
