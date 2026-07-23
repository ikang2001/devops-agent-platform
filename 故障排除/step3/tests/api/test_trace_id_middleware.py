from fastapi.testclient import TestClient

from devops_agent_platform.bootstrap.app import create_app


def test_trace_id_is_generated_when_header_is_missing() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.headers["X-Trace-Id"].startswith("trc_")
    assert response.json()["trace_id"] == response.headers["X-Trace-Id"]


def test_trace_id_header_is_propagated() -> None:
    client = TestClient(create_app())
    trace_id = "trc_test_123"

    response = client.get("/healthz", headers={"X-Trace-Id": trace_id})

    assert response.headers["X-Trace-Id"] == trace_id
    assert response.json()["trace_id"] == trace_id

