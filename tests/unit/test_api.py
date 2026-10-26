from fastapi.testclient import TestClient

from shorts_engine.api.main import app


def test_provider_settings_endpoint_is_reachable():
    client = TestClient(app)

    response = client.get("/api/settings/providers")

    assert response.status_code == 200
    assert response.json() == {"llm": "mock", "search": "mock"}


def test_root_serves_dashboard():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Shorts Engine Web" in response.text
