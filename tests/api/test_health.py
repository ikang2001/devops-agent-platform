from fastapi.testclient import TestClient

from devops_agent_platform.bootstrap.app import create_app


def test_healthz_returns_envelope() -> None:
    client = TestClient(create_app(runtime_enabled=False))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"
    assert body["error"] is None
    assert body["trace_id"].startswith("trc_")
