"""GET /health: "ok" or "not-ok" only, from the database check; never an AI provider or the CRM."""

import pytest
from fastapi.testclient import TestClient

from app import knowledge, main
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs startup, which syncs the guides into the test database
        yield c


def test_health_ok_when_the_database_holds_the_guides(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_health_not_ok_when_the_database_is_unreachable(client, monkeypatch):
    def unreachable(*args, **kwargs):
        raise main.psycopg.OperationalError("connection refused: secret-host:5432 password=hunter2")

    monkeypatch.setattr(main.psycopg, "connect", unreachable)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "not-ok"}
    # The reason never leaks: no host, no password, no exception text.
    assert "secret-host" not in response.text and "hunter2" not in response.text


def test_health_not_ok_when_the_collection_is_empty(client, monkeypatch):
    store = knowledge.get_store()

    class EmptyCollection:
        dsn = store.dsn
        collection = "no_such_collection"

    monkeypatch.setattr(knowledge, "get_store", lambda: EmptyCollection())
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "not-ok"}


def test_health_never_calls_a_provider_or_the_crm(client):
    # conftest replaces every real AI and CRM client factory with one that
    # fails the test; a 200 here means none was created.
    assert client.get("/health").status_code == 200


def test_health_takes_no_input_and_is_get_only(client):
    assert client.post("/health").status_code == 405
    assert client.get("/health?verbose=1").json() == {"status": "ok"}
