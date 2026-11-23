"""Swagger uses the same bearer dependency. It does not add a second login check."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PROTECTED = (
    ("/me", "get"),
    ("/orders", "get"),
    ("/orders/{order_id}", "get"),
    ("/tickets", "get"),
    ("/tickets/{ticket_id}", "get"),
    ("/tickets", "post"),
    ("/proposals/{proposal_id}/decision", "post"),
    ("/runs", "post"),
)


def test_openapi_advertises_one_bearer_scheme():
    spec = client.get("/openapi.json").json()
    scheme = spec["components"]["securitySchemes"]["HTTPBearer"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert scheme["bearerFormat"] == "JWT"
    assert list(spec["components"]["securitySchemes"]) == ["HTTPBearer"]
    for path, method in PROTECTED:
        assert spec["paths"][path][method]["security"] == [{"HTTPBearer": []}]
    assert "security" not in spec["paths"]["/health"]["get"]
    assert "security" not in spec["paths"]["/auth/token"]["post"]


def test_missing_and_non_bearer_headers_stay_401():
    missing = client.get("/me")
    lowercase = client.get("/me", headers={"Authorization": "bearer not-a-token"})
    empty = client.get("/me", headers={"Authorization": "Bearer "})
    invalid = client.get("/me", headers={"Authorization": "Bearer not-a-token"})
    assert missing.status_code == 401
    assert missing.json()["detail"] == "authentication required"
    assert lowercase.status_code == 401
    assert lowercase.json() == missing.json()
    assert empty.status_code == 401
    assert empty.json() == missing.json()
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "invalid token"
