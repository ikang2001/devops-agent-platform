import pytest
from fastapi.testclient import TestClient

from devops_agent_platform import __version__
from devops_agent_platform.bootstrap import app as app_module
from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.bootstrap.dependencies import (
    build_skeleton_alert_service,
    build_skeleton_rca_service,
)
from devops_agent_platform.bootstrap.readiness import (
    ComponentReadiness,
    ReadinessSnapshot,
)
from devops_agent_platform.infrastructure.config.settings import Settings


class FakeRuntime:
    """记录FastAPI lifespan对运行时的启停调用。"""

    def __init__(self) -> None:
        self.alert_service = build_skeleton_alert_service()
        self.rca_service = build_skeleton_rca_service()
        self.rca_cancellation_service = object()
        self.incident_query_service = object()
        self.incident_resolution_service = object()
        self.runbook_admin_service = object()
        self.ticket_draft_service = object()
        self.started = False
        self.closed = False
        self.readiness_snapshot = ReadinessSnapshot(
            components=(
                ("runtime", ComponentReadiness.UP),
                ("database", ComponentReadiness.UP),
                ("outbox_worker", ComponentReadiness.DISABLED),
            )
        )

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def check_readiness(self) -> ReadinessSnapshot:
        """返回不访问外部依赖的正常就绪快照。"""
        return self.readiness_snapshot


def test_openapi_version_uses_installed_package_metadata() -> None:
    app = create_app(runtime_enabled=False)

    assert app.version == __version__


def test_production_cannot_disable_runtime_and_fall_back_to_skeleton() -> None:
    """生产装配必须拒绝显式 Skeleton 回退。"""
    settings = Settings(
        _env_file=None,
        app_env="production",
        alert_webhook_auth_enabled=True,
        alert_webhook_secret="x" * 32,
    )

    with pytest.raises(ValueError, match="Skeleton runtime"):
        create_app(settings=settings, runtime_enabled=False)


def test_lifespan_starts_runtime_and_injects_application_service() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda settings: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/healthz")

        assert response.status_code == 200
        assert runtime.started is True
        assert app.state.alert_application_service is runtime.alert_service
        assert (
            app.state.incident_query_service
            is runtime.incident_query_service
        )
        assert (
            app.state.rca_cancellation_service
            is runtime.rca_cancellation_service
        )
        assert (
            app.state.incident_resolution_service
            is runtime.incident_resolution_service
        )
        assert (
            app.state.runbook_admin_service
            is runtime.runbook_admin_service
        )
        assert (
            app.state.ticket_draft_service
            is runtime.ticket_draft_service
        )

    assert runtime.closed is True
    assert app.state.alert_application_service is None
    assert app.state.rca_cancellation_service is None
    assert app.state.incident_query_service is None
    assert app.state.incident_resolution_service is None
    assert app.state.runbook_admin_service is None
    assert app.state.ticket_draft_service is None
    assert app.state.runtime is None


def test_default_runtime_receives_app_local_metrics(monkeypatch) -> None:
    """默认生产装配应复用当前App的独立Metrics观察端口。"""
    runtime = FakeRuntime()
    captured = {}

    def fake_build_runtime(
        settings,
        report_observer=None,
        ticketing_gateway_observer=None,
    ):
        del settings
        captured["report_observer"] = report_observer
        captured["ticketing_gateway_observer"] = ticketing_gateway_observer
        return runtime

    monkeypatch.setattr(app_module, "build_runtime", fake_build_runtime)
    app = create_app()

    with TestClient(app):
        assert captured["report_observer"] is app.state.metrics
        assert captured["ticketing_gateway_observer"] is app.state.metrics

    assert runtime.closed is True


def test_business_request_returns_503_when_runtime_is_not_ready() -> None:
    app = create_app(runtime_enabled=False)
    app.state.alert_application_service = None
    client = TestClient(app)

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
            "external_event_id": "evt-001",
        },
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "RUNTIME_UNAVAILABLE"
    assert body["trace_id"] == response.headers["X-Trace-Id"]


def test_readyz_returns_component_snapshot() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda settings: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {
        "status": "ready",
        "components": {
            "runtime": {"status": "up"},
            "database": {"status": "up"},
            "outbox_worker": {"status": "disabled"},
        },
    }


def test_readyz_returns_503_without_started_runtime() -> None:
    client = TestClient(create_app(runtime_enabled=False))

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "RUNTIME_UNAVAILABLE"
    assert body["trace_id"] == response.headers["X-Trace-Id"]


def test_readyz_returns_503_when_database_is_down() -> None:
    runtime = FakeRuntime()
    runtime.readiness_snapshot = ReadinessSnapshot(
        components=(
            ("runtime", ComponentReadiness.UP),
            ("database", ComponentReadiness.DOWN),
            ("outbox_worker", ComponentReadiness.DISABLED),
        )
    )
    app = create_app(runtime_factory=lambda settings: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == {
        "code": "RUNTIME_UNAVAILABLE",
        "message": "Components not ready: database",
    }
