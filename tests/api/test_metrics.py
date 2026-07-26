import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from devops_agent_platform.application.services import (
    ticket_submission_consumer_runner as ticket_consumer_runner,
)
from devops_agent_platform.application.services.outbox_backlog import (
    OutboxBacklogReport,
)
from devops_agent_platform.application.services.rca_consumer_runner import (
    RCAConsumerHealth,
)
from devops_agent_platform.application.services.remediation_reclaim_worker import (
    RemediationReclaimWorkerHealth,
)
from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.bootstrap.dependencies import (
    build_skeleton_alert_service,
    build_skeleton_rca_service,
)
from devops_agent_platform.bootstrap.readiness import (
    ComponentReadiness,
    ReadinessSnapshot,
)
from devops_agent_platform.domain.enums import (
    OutboxStatus,
    RCAConsumerWorkerState,
    RemediationReclaimWorkerState,
    TicketSubmissionConsumerWorkerState,
)
from devops_agent_platform.infrastructure.config.settings import Settings
from devops_agent_platform.ports.outbox_metrics import OutboxBacklogSnapshot

TicketSubmissionConsumerHealth = ticket_consumer_runner.TicketSubmissionConsumerHealth


class FixedBacklogMonitor:
    """返回固定积压报告的监控服务替身。"""

    def __init__(
        self,
        report: OutboxBacklogReport,
        error: Exception | None = None,
    ) -> None:
        self._report = report
        self._error = error

    async def collect(self) -> OutboxBacklogReport:
        if self._error is not None:
            raise self._error
        return self._report


class MetricsRuntime:
    """为metrics端到端测试提供最小生命周期和健康状态。"""

    def __init__(
        self,
        monitor: FixedBacklogMonitor,
        rca_consumer_health: RCAConsumerHealth | None = None,
        ticket_submission_consumer_health: (
            TicketSubmissionConsumerHealth | None
        ) = None,
        remediation_reclaim_health: (RemediationReclaimWorkerHealth | None) = None,
        readiness_error: Exception | None = None,
    ) -> None:
        self.alert_service = build_skeleton_alert_service()
        self.rca_service = build_skeleton_rca_service()
        self.outbox_backlog_monitor = monitor
        self.worker_health = None
        self.rca_consumer_health = rca_consumer_health
        self.ticket_submission_consumer_health = ticket_submission_consumer_health
        self.remediation_reclaim_health = remediation_reclaim_health
        self._readiness_error = readiness_error

    async def start(self) -> None:
        """模拟Runtime启动。"""

    async def close(self) -> None:
        """模拟Runtime关闭。"""

    async def check_readiness(self) -> ReadinessSnapshot:
        if self._readiness_error is not None:
            raise self._readiness_error
        return ReadinessSnapshot(
            components=(
                ("runtime", ComponentReadiness.UP),
                ("database", ComponentReadiness.UP),
                ("outbox_worker", ComponentReadiness.DISABLED),
            )
        )


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


def test_metrics_endpoint_exposes_http_golden_signals() -> None:
    client = TestClient(create_app(runtime_enabled=False))
    response = client.post(
        "/api/v1/alerts?token=must-not-appear",
        json=_alert_payload(),
    )
    assert response.status_code == 501

    metrics_response = client.get("/metrics")

    assert metrics_response.status_code == 200
    assert metrics_response.headers["content-type"].startswith("text/plain")
    assert metrics_response.headers["X-Trace-Id"].startswith("trc_")
    body = metrics_response.text
    assert (
        'devops_agent_http_requests_total{method="POST",'
        'route="/api/v1/alerts",status_class="5xx"} 1.0'
    ) in body
    assert (
        'devops_agent_http_request_duration_seconds_count{method="POST",'
        'route="/api/v1/alerts"} 1.0'
    ) in body
    assert "must-not-appear" not in body
    assert 'component="runtime"} 0.0' in body


def test_unmatched_paths_and_unknown_methods_use_bounded_labels() -> None:
    client = TestClient(create_app(runtime_enabled=False))

    assert client.get("/random/id-001").status_code == 404
    assert client.get("/random/id-002").status_code == 404
    assert client.request("BREW", "/random/id-003").status_code == 404
    body = client.get("/metrics").text

    assert (
        'devops_agent_http_requests_total{method="GET",route="unmatched",'
        'status_class="4xx"} 2.0'
    ) in body
    assert (
        'devops_agent_http_requests_total{method="OTHER",route="unmatched",'
        'status_class="4xx"} 1.0'
    ) in body
    assert "id-001" not in body
    assert "id-002" not in body
    assert "id-003" not in body


def test_metrics_endpoint_does_not_measure_itself() -> None:
    client = TestClient(create_app(runtime_enabled=False))

    first = client.get("/metrics")
    second = client.get("/metrics")

    assert first.status_code == 200
    assert second.status_code == 200
    assert 'route="/metrics"' not in second.text


def test_metrics_can_be_disabled() -> None:
    settings = Settings(_env_file=None, metrics_enabled=False)
    client = TestClient(
        create_app(
            settings=settings,
            runtime_enabled=False,
        )
    )

    response = client.get("/metrics")

    assert response.status_code == 404


def test_metrics_endpoint_exposes_database_backlog_snapshot() -> None:
    oldest = datetime.now(UTC) - timedelta(seconds=30)
    report = OutboxBacklogReport(
        snapshot=OutboxBacklogSnapshot(
            counts=(
                (OutboxStatus.PENDING, 8),
                (OutboxStatus.PROCESSING, 2),
                (OutboxStatus.FAILED, 1),
            ),
            oldest_unpublished_at=oldest,
        ),
        source_up=True,
        stale=False,
    )
    runtime = MetricsRuntime(FixedBacklogMonitor(report))
    app = create_app(
        runtime_factory=lambda settings: runtime,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        body = client.get("/metrics").text

    assert ('devops_agent_outbox_backlog_events{status="PENDING"} 8.0') in body
    assert "devops_agent_outbox_backlog_source_up 1.0" in body
    assert "devops_agent_outbox_backlog_stale 0.0" in body
    age_line = next(
        line
        for line in body.splitlines()
        if line.startswith("devops_agent_outbox_oldest_unpublished_age_seconds ")
    )
    assert 29 <= float(age_line.rsplit(" ", 1)[1]) <= 35


def test_metrics_refresh_logs_exclude_dependency_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """局部刷新失败仍应返回指标，但日志不能泄露依赖异常正文。"""
    report = OutboxBacklogReport(
        snapshot=OutboxBacklogSnapshot(
            counts=(),
            oldest_unpublished_at=None,
        ),
        source_up=False,
        stale=True,
    )
    runtime = MetricsRuntime(
        FixedBacklogMonitor(
            report,
            error=RuntimeError("password=outbox-secret"),
        ),
        readiness_error=RuntimeError("token=runtime-secret"),
    )
    app = create_app(
        runtime_factory=lambda settings: runtime,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        with caplog.at_level(
            logging.ERROR,
            logger=("devops_agent_platform.interfaces.http.routes.metrics"),
        ):
            response = client.get("/metrics")

    assert response.status_code == 200
    assert [record.getMessage() for record in caplog.records] == [
        "刷新Runtime指标失败",
        "刷新Outbox积压指标失败",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "runtime-secret" not in caplog.text
    assert "outbox-secret" not in caplog.text


def test_metrics_endpoint_exposes_rca_consumer_snapshot() -> None:
    """指标端点应导出低基数 RCA Consumer 健康快照。"""
    now = datetime.now(UTC)
    report = OutboxBacklogReport(
        snapshot=OutboxBacklogSnapshot(
            counts=(),
            oldest_unpublished_at=None,
        ),
        source_up=True,
        stale=False,
    )
    health = RCAConsumerHealth(
        state=RCAConsumerWorkerState.RUNNING,
        started_at=now - timedelta(minutes=1),
        stopped_at=None,
        last_cycle_at=now,
        last_success_at=now,
        last_error=None,
        consecutive_failures=0,
        total_polled=5,
        total_acknowledged=4,
        total_retried=1,
        total_dead_lettered=0,
        lag_source_up=True,
        assigned_partitions=3,
        measured_partitions=3,
        total_lag=25,
        max_partition_lag=12,
    )
    runtime = MetricsRuntime(
        FixedBacklogMonitor(report),
        rca_consumer_health=health,
    )
    app = create_app(
        runtime_factory=lambda settings: runtime,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        body = client.get("/metrics").text

    assert "devops_agent_rca_consumer_enabled 1.0" in body
    assert 'devops_agent_rca_consumer_state{state="RUNNING"} 1.0' in body
    assert 'devops_agent_rca_consumer_records{result="acknowledged"} 4.0' in body
    assert "devops_agent_rca_consumer_lag_source_up 1.0" in body
    assert 'devops_agent_rca_consumer_lag_records{scope="total"} 25.0' in body
    assert "tenant_id" not in body
    assert "workflow_run_id" not in body


def test_metrics_endpoint_exposes_ticket_submission_consumer_snapshot() -> None:
    """指标端点应导出低基数工单提交 Consumer 健康快照。"""
    now = datetime.now(UTC)
    report = OutboxBacklogReport(
        snapshot=OutboxBacklogSnapshot(
            counts=(),
            oldest_unpublished_at=None,
        ),
        source_up=True,
        stale=False,
    )
    health = TicketSubmissionConsumerHealth(
        state=TicketSubmissionConsumerWorkerState.DEGRADED,
        started_at=now - timedelta(minutes=1),
        stopped_at=None,
        last_cycle_at=now,
        last_success_at=now - timedelta(seconds=20),
        last_error="Ticketing gateway timeout",
        consecutive_failures=2,
        total_polled=7,
        total_acknowledged=3,
        total_retried=2,
        total_dead_lettered=1,
        total_ignored=1,
    )
    runtime = MetricsRuntime(
        FixedBacklogMonitor(report),
        ticket_submission_consumer_health=health,
    )
    app = create_app(
        runtime_factory=lambda settings: runtime,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        body = client.get("/metrics").text

    assert "devops_agent_ticket_submission_consumer_enabled 1.0" in body
    assert 'devops_agent_ticket_submission_consumer_state{state="DEGRADED"} 1.0' in body
    assert (
        'devops_agent_ticket_submission_consumer_records{result="retried"} 2.0' in body
    )
    assert (
        'devops_agent_ticket_submission_consumer_records{result="ignored"} 1.0' in body
    )
    assert "devops_agent_ticket_submission_consumer_consecutive_failures 2.0" in body
    assert "tenant_id" not in body
    assert "target_system" not in body


def test_metrics_endpoint_exposes_remediation_reclaim_snapshot() -> None:
    """指标端点应导出低基数租约回收 Worker 健康快照。"""
    now = datetime.now(UTC)
    report = OutboxBacklogReport(
        snapshot=OutboxBacklogSnapshot(
            counts=(),
            oldest_unpublished_at=None,
        ),
        source_up=True,
        stale=False,
    )
    health = RemediationReclaimWorkerHealth(
        state=RemediationReclaimWorkerState.RUNNING,
        started_at=now - timedelta(minutes=1),
        stopped_at=None,
        last_cycle_at=now,
        last_success_at=now,
        last_error=None,
        consecutive_failures=0,
        total_cycles=8,
        total_reclaimed_plans=3,
    )
    runtime = MetricsRuntime(
        FixedBacklogMonitor(report),
        remediation_reclaim_health=health,
    )
    app = create_app(
        runtime_factory=lambda settings: runtime,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        body = client.get("/metrics").text

    assert "devops_agent_remediation_reclaim_worker_enabled 1.0" in body
    assert 'devops_agent_remediation_reclaim_worker_state{state="RUNNING"} 1.0' in body
    assert "devops_agent_remediation_reclaim_worker_cycles 8.0" in body
    assert "devops_agent_remediation_reclaimed_plans 3.0" in body
    assert "tenant_id" not in body
    assert "remediation_plan_id" not in body
