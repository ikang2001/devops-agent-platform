from fastapi.testclient import TestClient

from devops_agent_platform.bootstrap.app import create_app


def test_alert_route_returns_not_implemented_envelope() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/alerts",
        json={
            "tenant_id": "tenant-a",
            "source": "alertmanager",
            "service_name": "checkout-api",
            "severity": "CRITICAL",
            "summary": "5xx error rate is high",
            "starts_at": "2026-06-27T10:00:00Z",
            "fingerprint": "fp-001",
        },
    )

    assert response.status_code == 501
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_IMPLEMENTED"
    assert body["trace_id"] == response.headers["X-Trace-Id"]


def test_rca_route_returns_not_implemented_envelope() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/incidents/inc-001/rca",
        json={
            "tenant_id": "tenant-a",
            "operator_id": "user-001",
        },
    )

    assert response.status_code == 501
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_IMPLEMENTED"
    assert body["trace_id"] == response.headers["X-Trace-Id"]

