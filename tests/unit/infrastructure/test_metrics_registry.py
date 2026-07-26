from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.services import (
    ticket_submission_consumer_runner as ticket_consumer_runner,
)
from devops_agent_platform.application.services.audit_retention_worker import (
    AuditRetentionWorkerHealth,
)
from devops_agent_platform.application.services.outbox_backlog import (
    OutboxBacklogReport,
)
from devops_agent_platform.application.services.outbox_worker import (
    OutboxWorkerHealth,
)
from devops_agent_platform.application.services.rca_consumer_runner import (
    RCAConsumerHealth,
)
from devops_agent_platform.application.services.remediation_reclaim_worker import (
    RemediationReclaimWorkerHealth,
)
from devops_agent_platform.domain.enums import (
    AuditRetentionWorkerState,
    OutboxStatus,
    OutboxWorkerState,
    RCAConsumerWorkerState,
    RemediationReclaimWorkerState,
    TicketSubmissionConsumerWorkerState,
)
from devops_agent_platform.infrastructure.metrics import ApplicationMetrics
from devops_agent_platform.ports.outbox_metrics import OutboxBacklogSnapshot
from devops_agent_platform.ports.rca_report import (
    LLMReportGenerationOutcome,
)
from devops_agent_platform.ports.ticketing import (
    TicketingGatewaySubmitOutcome,
)

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
TicketSubmissionConsumerHealth = ticket_consumer_runner.TicketSubmissionConsumerHealth


def test_each_application_uses_an_independent_registry() -> None:
    first = ApplicationMetrics()
    second = ApplicationMetrics()

    first_method = first.start_http_request("GET")
    first.finish_http_request(
        method=first_method,
        route="/api/v1/alerts",
        status_code=200,
        duration_seconds=0.1,
    )

    assert first.registry is not second.registry
    assert (
        first.registry.get_sample_value(
            "devops_agent_http_requests_total",
            {"method": "GET", "route": "/api/v1/alerts", "status_class": "2xx"},
        )
        == 1
    )
    assert (
        second.registry.get_sample_value(
            "devops_agent_http_requests_total",
            {"method": "GET", "route": "/api/v1/alerts", "status_class": "2xx"},
        )
        is None
    )


def test_http_labels_are_normalized_and_in_progress_returns_to_zero() -> None:
    metrics = ApplicationMetrics()

    method = metrics.start_http_request("BREW")
    metrics.finish_http_request(
        method=method,
        route="unmatched",
        status_code=799,
        duration_seconds=-1,
    )

    assert method == "OTHER"
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_http_requests_total",
            {"method": "OTHER", "route": "unmatched", "status_class": "other"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_http_requests_in_progress",
            {"method": "OTHER"},
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_http_request_duration_seconds_sum",
            {"method": "OTHER", "route": "unmatched"},
        )
        == 0
    )


def test_concurrent_http_updates_do_not_lose_counts() -> None:
    metrics = ApplicationMetrics()

    def record_request() -> None:
        method = metrics.start_http_request("POST")
        metrics.finish_http_request(
            method=method,
            route="/api/v1/alerts",
            status_code=202,
            duration_seconds=0.01,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: record_request(), range(100)))

    assert (
        metrics.registry.get_sample_value(
            "devops_agent_http_requests_total",
            {"method": "POST", "route": "/api/v1/alerts", "status_class": "2xx"},
        )
        == 100
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_http_requests_in_progress",
            {"method": "POST"},
        )
        == 0
    )


def test_runtime_and_worker_gauges_are_refreshed_from_snapshots() -> None:
    metrics = ApplicationMetrics()
    worker_health = OutboxWorkerHealth(
        state=OutboxWorkerState.DEGRADED,
        started_at=NOW,
        stopped_at=None,
        last_cycle_at=NOW,
        last_success_at=None,
        last_error="database unavailable",
        consecutive_failures=3,
        total_claimed=10,
        total_published=7,
        total_retried=2,
        total_failed=1,
    )

    metrics.update_runtime(
        (
            ("runtime", "up"),
            ("database", "down"),
            ("outbox_worker", "up"),
        ),
        worker_health,
    )

    assert (
        metrics.registry.get_sample_value(
            "devops_agent_runtime_component_ready",
            {"component": "runtime"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_runtime_component_ready",
            {"component": "database"},
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_outbox_worker_state",
            {"state": "DEGRADED"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_outbox_worker_messages",
            {"result": "published"},
        )
        == 7
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_outbox_worker_consecutive_failures"
        )
        == 3
    )


def test_rca_consumer_gauges_are_refreshed_and_reset() -> None:
    """RCA健康快照应覆盖状态、累计结果和最近活动时间。"""
    metrics = ApplicationMetrics()
    health = RCAConsumerHealth(
        state=RCAConsumerWorkerState.DEGRADED,
        started_at=NOW - timedelta(minutes=5),
        stopped_at=None,
        last_cycle_at=NOW - timedelta(seconds=5),
        last_success_at=NOW - timedelta(seconds=30),
        last_error="Kafka message requires retry",
        consecutive_failures=2,
        total_polled=12,
        total_acknowledged=8,
        total_retried=3,
        total_dead_lettered=1,
        lag_source_up=True,
        assigned_partitions=4,
        measured_partitions=4,
        total_lag=120,
        max_partition_lag=50,
    )

    metrics.update_rca_consumer(health)

    assert metrics.registry.get_sample_value("devops_agent_rca_consumer_enabled") == 1
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_rca_consumer_state",
            {"state": "DEGRADED"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_rca_consumer_records",
            {"result": "dead_lettered"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_rca_consumer_last_success_timestamp_seconds"
        )
        == (NOW - timedelta(seconds=30)).timestamp()
    )
    assert (
        metrics.registry.get_sample_value("devops_agent_rca_consumer_lag_source_up")
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_rca_consumer_lag_records",
            {"scope": "total"},
        )
        == 120
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_rca_consumer_lag_records",
            {"scope": "max_partition"},
        )
        == 50
    )

    metrics.update_rca_consumer(None)

    assert metrics.registry.get_sample_value("devops_agent_rca_consumer_enabled") == 0
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_rca_consumer_state",
            {"state": "DEGRADED"},
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_rca_consumer_records",
            {"result": "retried"},
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_rca_consumer_last_cycle_timestamp_seconds"
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value("devops_agent_rca_consumer_lag_source_up")
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_rca_consumer_lag_records",
            {"scope": "total"},
        )
        == 0
    )


def test_ticket_submission_consumer_gauges_are_refreshed_and_reset() -> None:
    """工单提交健康快照应覆盖状态、结果计数和最近活动时间。"""
    metrics = ApplicationMetrics()
    health = TicketSubmissionConsumerHealth(
        state=TicketSubmissionConsumerWorkerState.DEGRADED,
        started_at=NOW - timedelta(minutes=2),
        stopped_at=None,
        last_cycle_at=NOW - timedelta(seconds=10),
        last_success_at=NOW - timedelta(seconds=45),
        last_error="Ticketing gateway timeout",
        consecutive_failures=4,
        total_polled=20,
        total_acknowledged=12,
        total_retried=5,
        total_dead_lettered=2,
        total_ignored=1,
        lag_source_up=True,
        assigned_partitions=3,
        measured_partitions=3,
        total_lag=90,
        max_partition_lag=40,
    )

    metrics.update_ticket_submission_consumer(health)

    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_enabled"
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_state",
            {"state": "DEGRADED"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_records",
            {"result": "retried"},
        )
        == 5
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_records",
            {"result": "ignored"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_last_success_timestamp_seconds"
        )
        == (NOW - timedelta(seconds=45)).timestamp()
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_consecutive_failures"
        )
        == 4
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_lag_source_up"
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_lag_records",
            {"scope": "total"},
        )
        == 90
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_lag_records",
            {"scope": "max_partition"},
        )
        == 40
    )

    metrics.update_ticket_submission_consumer(None)

    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_enabled"
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_state",
            {"state": "DEGRADED"},
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_records",
            {"result": "dead_lettered"},
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_last_cycle_timestamp_seconds"
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_lag_source_up"
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticket_submission_consumer_lag_records",
            {"scope": "total"},
        )
        == 0
    )


def test_audit_retention_gauges_are_refreshed_and_reset() -> None:
    """审计留存清理健康快照应覆盖状态、时间戳和累计清理量。"""
    metrics = ApplicationMetrics()
    health = AuditRetentionWorkerHealth(
        state=AuditRetentionWorkerState.DEGRADED,
        started_at=NOW - timedelta(hours=1),
        stopped_at=None,
        last_cycle_at=NOW - timedelta(seconds=20),
        last_success_at=NOW - timedelta(minutes=5),
        last_error="database unavailable",
        consecutive_failures=2,
        total_cycles=17,
        total_purged_workflow_runs=230,
    )

    metrics.update_audit_retention(health)

    assert (
        metrics.registry.get_sample_value("devops_agent_audit_retention_worker_enabled")
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_audit_retention_worker_state",
            {"state": "DEGRADED"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_audit_retention_worker_consecutive_failures"
        )
        == 2
    )
    assert (
        metrics.registry.get_sample_value("devops_agent_audit_retention_worker_cycles")
        == 17
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_audit_retention_purged_workflow_runs"
        )
        == 230
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_audit_retention_worker_last_success_timestamp_seconds"
        )
        == (NOW - timedelta(minutes=5)).timestamp()
    )

    metrics.update_audit_retention(None)

    assert (
        metrics.registry.get_sample_value("devops_agent_audit_retention_worker_enabled")
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_audit_retention_worker_state",
            {"state": "DEGRADED"},
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value("devops_agent_audit_retention_worker_cycles")
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_audit_retention_worker_last_cycle_timestamp_seconds"
        )
        == 0
    )


def test_remediation_reclaim_gauges_are_refreshed_and_reset() -> None:
    """租约回收健康快照应覆盖状态、时间戳和累计收口量。"""
    metrics = ApplicationMetrics()
    health = RemediationReclaimWorkerHealth(
        state=RemediationReclaimWorkerState.DEGRADED,
        started_at=NOW - timedelta(hours=1),
        stopped_at=None,
        last_cycle_at=NOW - timedelta(seconds=20),
        last_success_at=NOW - timedelta(minutes=5),
        last_error="database unavailable",
        consecutive_failures=2,
        total_cycles=17,
        total_reclaimed_plans=9,
    )

    metrics.update_remediation_reclaim(health)

    assert (
        metrics.registry.get_sample_value(
            "devops_agent_remediation_reclaim_worker_enabled"
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_remediation_reclaim_worker_state",
            {"state": "DEGRADED"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_remediation_reclaim_worker_consecutive_failures"
        )
        == 2
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_remediation_reclaim_worker_cycles"
        )
        == 17
    )
    assert (
        metrics.registry.get_sample_value("devops_agent_remediation_reclaimed_plans")
        == 9
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_remediation_reclaim_worker_last_success_timestamp_seconds"
        )
        == (NOW - timedelta(minutes=5)).timestamp()
    )

    metrics.update_remediation_reclaim(None)

    assert (
        metrics.registry.get_sample_value(
            "devops_agent_remediation_reclaim_worker_enabled"
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_remediation_reclaim_worker_state",
            {"state": "DEGRADED"},
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_remediation_reclaim_worker_cycles"
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_remediation_reclaim_worker_last_cycle_timestamp_seconds"
        )
        == 0
    )


def test_llm_report_metrics_use_fixed_outcomes_and_duration() -> None:
    """LLM观察端口应累计固定结果并记录对应耗时。"""
    metrics = ApplicationMetrics()

    metrics.observe_llm_report(
        LLMReportGenerationOutcome.FALLBACK_TIMEOUT,
        1.25,
    )

    assert (
        metrics.registry.get_sample_value(
            "devops_agent_llm_report_generation_total",
            {"outcome": "FALLBACK_TIMEOUT"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_llm_report_generation_duration_seconds_sum",
            {"outcome": "FALLBACK_TIMEOUT"},
        )
        == 1.25
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_llm_report_generation_total",
            {"outcome": "FALLBACK_CIRCUIT_OPEN"},
        )
        == 0
    )


def test_ticketing_gateway_metrics_use_fixed_outcomes_and_duration() -> None:
    """外部工单网关观察端口应累计固定结果并记录耗时。"""
    metrics = ApplicationMetrics()

    metrics.observe_ticketing_gateway_submit(
        TicketingGatewaySubmitOutcome.SUCCESS,
        0.75,
    )

    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "SUCCESS"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_duration_seconds_sum",
            {"outcome": "SUCCESS"},
        )
        == 0.75
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "GATEWAY_ERROR"},
        )
        == 0
    )


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), True])
def test_ticketing_gateway_metrics_reject_invalid_duration(
    duration: float,
) -> None:
    """非法耗时不能污染工单网关 Histogram 状态。"""
    metrics = ApplicationMetrics()

    with pytest.raises(ValueError, match="duration_seconds"):
        metrics.observe_ticketing_gateway_submit(
            TicketingGatewaySubmitOutcome.SUCCESS,
            duration,
        )


def test_ticketing_gateway_metrics_reject_invalid_outcome() -> None:
    """调用方必须使用固定枚举，不能注入动态标签。"""
    metrics = ApplicationMetrics()

    with pytest.raises(ValueError, match="TicketingGatewaySubmitOutcome"):
        metrics.observe_ticketing_gateway_submit(
            "jira",  # type: ignore[arg-type]
            0.1,
        )


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), True])
def test_llm_report_metrics_reject_invalid_duration(duration: float) -> None:
    """非法耗时不能污染Histogram状态。"""
    metrics = ApplicationMetrics()

    with pytest.raises(ValueError, match="duration_seconds"):
        metrics.observe_llm_report(
            LLMReportGenerationOutcome.SUCCESS,
            duration,
        )


def test_outbox_backlog_gauges_include_age_and_stale_state() -> None:
    metrics = ApplicationMetrics()
    snapshot = OutboxBacklogSnapshot(
        counts=(
            (OutboxStatus.PENDING, 8),
            (OutboxStatus.PROCESSING, 2),
            (OutboxStatus.FAILED, 1),
        ),
        oldest_unpublished_at=NOW - timedelta(seconds=90),
    )
    report = OutboxBacklogReport(
        snapshot=snapshot,
        source_up=False,
        stale=True,
    )

    metrics.update_outbox_backlog(report, now=NOW)

    assert (
        metrics.registry.get_sample_value(
            "devops_agent_outbox_backlog_events",
            {"status": "PENDING"},
        )
        == 8
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_outbox_oldest_unpublished_age_seconds"
        )
        == 90
    )
    assert (
        metrics.registry.get_sample_value("devops_agent_outbox_backlog_source_up") == 0
    )
    assert metrics.registry.get_sample_value("devops_agent_outbox_backlog_stale") == 1
