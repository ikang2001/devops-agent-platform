import logging

from fastapi.testclient import TestClient

from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.logging.context import (
    get_current_trace_id,
)

ACCESS_LOGGER = "devops_agent_platform.http.access"


def _alert_payload() -> dict[str, str]:
    """返回通过HTTP参数校验的最小告警请求。"""
    return {
        "tenant_id": "tenant-a",
        "source": "alertmanager",
        "service_name": "checkout-api",
        "severity": "CRITICAL",
        "summary": "5xx error rate is high",
        "starts_at": "2026-06-27T10:00:00Z",
        "fingerprint": "fp-001",
        "external_event_id": "evt-001",
    }


def test_access_log_contains_bounded_request_metadata(caplog) -> None:
    app = create_app(runtime_enabled=False)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING, logger=ACCESS_LOGGER):
        response = client.post(
            "/api/v1/alerts?token=must-not-appear",
            json=_alert_payload(),
            headers={
                "X-Trace-Id": "trc_access_test",
                "Authorization": "Bearer must-not-appear",
            },
        )

    assert response.status_code == 501
    record = next(record for record in caplog.records if record.name == ACCESS_LOGGER)
    assert record.http_method == "POST"
    assert record.http_path == "/api/v1/alerts"
    assert record.http_status_code == 501
    assert record.duration_ms >= 0
    assert "must-not-appear" not in record.getMessage()
    assert get_current_trace_id() is None


def test_dynamic_resource_id_is_replaced_by_route_template(caplog) -> None:
    app = create_app(runtime_enabled=False)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING, logger=ACCESS_LOGGER):
        response = client.post(
            (
                "/api/v1/admin/tenants/tenant-sensitive-value"
                "/incidents/inc-sensitive-value/rca"
            ),
            headers={
                "Authorization": "Bearer token-access-001",
                "Idempotency-Key": "idem-access-001",
            },
        )

    assert response.status_code == 503
    record = next(record for record in caplog.records if record.name == ACCESS_LOGGER)
    assert record.http_path == (
        "/api/v1/admin/tenants/{tenant_id}/incidents/{incident_id}/rca"
    )


def test_invalid_external_trace_id_is_replaced() -> None:
    client = TestClient(create_app(runtime_enabled=False))

    response = client.get(
        "/healthz",
        headers={"X-Trace-Id": "x" * 129},
    )

    trace_id = response.headers["X-Trace-Id"]
    assert trace_id.startswith("trc_")
    assert trace_id != "x" * 129


def test_health_access_log_is_suppressed_by_default(caplog) -> None:
    app = create_app(runtime_enabled=False)
    client = TestClient(app)

    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        response = client.get("/healthz")

    assert response.status_code == 200
    assert not [record for record in caplog.records if record.name == ACCESS_LOGGER]


def test_unexpected_exception_returns_safe_envelope_and_is_logged(caplog) -> None:
    app = create_app(runtime_enabled=False)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal-sensitive-detail")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger=ACCESS_LOGGER):
        response = client.get("/boom", headers={"X-Trace-Id": "trc_boom"})

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "data": None,
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "Internal server error",
        },
        "trace_id": "trc_boom",
    }
    record = next(record for record in caplog.records if record.name == ACCESS_LOGGER)
    assert record.exc_info is None
    assert record.http_status_code == 500
    assert "internal-sensitive-detail" not in caplog.text


def test_app_exception_message_is_redacted_at_http_boundary() -> None:
    """应用异常消息进入HTTP响应前必须兜底脱敏。"""
    app = create_app(runtime_enabled=False)

    @app.get("/app-error")
    async def app_error() -> None:
        raise AppValidationError(
            "invalid password=hunter2 token=secret-token"
        )

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/app-error",
        headers={"X-Trace-Id": "trc_app_error"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == (
        "invalid password=[REDACTED] token=[REDACTED]"
    )
    assert "hunter2" not in response.text
    assert "secret-token" not in response.text
    assert body["trace_id"] == "trc_app_error"
