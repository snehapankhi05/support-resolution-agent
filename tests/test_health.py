from fastapi.testclient import TestClient

from app.main import app


def test_health_ok_when_db_up(engine):  # `engine` fixture skips the test if Postgres is down
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
