from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_ready_without_db():
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not ready"
