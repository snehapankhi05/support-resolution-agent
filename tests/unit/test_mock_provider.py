"""Idempotency of the mock provider. No database and no refund execution."""

from fastapi.testclient import TestClient

from mock_services.payments.app import create_app


def test_same_key_and_payload_returns_the_same_operation(tmp_path):
    client = TestClient(create_app(tmp_path / "provider.sqlite"))
    body = {"idempotency_key": "refund-1", "amount_minor": 249900, "order_id": "order-1"}
    first = client.post("/refunds", json=body)
    second = client.post("/refunds", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert client.get("/stats").json() == {"attempts": 1, "operations": 1}


def test_same_key_with_a_different_payload_is_rejected(tmp_path):
    client = TestClient(create_app(tmp_path / "provider.sqlite"))
    first = {"idempotency_key": "refund-1", "amount_minor": 249900, "order_id": "order-1"}
    changed = {"idempotency_key": "refund-1", "amount_minor": 100, "order_id": "order-1"}
    assert client.post("/refunds", json=first).status_code == 200
    conflict = client.post("/refunds", json=changed)
    assert conflict.status_code == 409
    assert client.get("/stats").json()["operations"] == 1


def test_error_500_does_not_store_an_operation(tmp_path):
    client = TestClient(create_app(tmp_path / "provider.sqlite"))
    assert client.post("/control", json={"mode": "error_500"}).status_code == 200
    response = client.post(
        "/refunds", json={"idempotency_key": "refund-1", "amount_minor": 100, "order_id": "o"}
    )
    assert response.status_code == 500
    assert client.get("/refunds/refund-1").status_code == 404
    assert client.get("/stats").json() == {"attempts": 1, "operations": 0}


def test_restart_replays_the_same_operation(tmp_path):
    path = tmp_path / "provider.sqlite"
    body = {"idempotency_key": "refund-1", "amount_minor": 249900, "order_id": "order-1"}
    first = TestClient(create_app(path)).post("/refunds", json=body)
    second = TestClient(create_app(path)).post("/refunds", json=body)
    conflict = TestClient(create_app(path)).post(
        "/refunds",
        json={"idempotency_key": "refund-1", "amount_minor": 100, "order_id": "order-1"},
    )
    assert first.status_code == 200
    assert second.json() == first.json()
    assert conflict.status_code == 409

