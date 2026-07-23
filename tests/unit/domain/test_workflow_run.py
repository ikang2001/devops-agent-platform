from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.workflow_run import WorkflowRun

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def build_workflow_run(**overrides: object) -> WorkflowRun:
    """构造有效的待执行WorkflowRun。"""
    values: dict[str, object] = {
        "workflow_run_id": "wfr_001",
        "tenant_id": "tenant_001",
        "incident_id": "inc_001",
        "operator_id": "user_001",
        "idempotency_key_hash": "a" * 64,
        "request_hash": "b" * 64,
        "trace_id": "trc_001",
        "status": WorkflowRunStatus.PENDING,
        "created_at": NOW,
        "updated_at": NOW,
        "started_at": None,
        "ended_at": None,
        "step_count": 0,
        "version": 1,
    }
    values.update(overrides)
    return WorkflowRun(**values)  # type: ignore[arg-type]


def test_workflow_run_moves_through_success_path() -> None:
    workflow_run = build_workflow_run()

    workflow_run.start(
        NOW + timedelta(seconds=1),
        lease_owner="worker_001",
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    workflow_run.record_step(NOW + timedelta(seconds=2))
    workflow_run.mark_succeeded(
        NOW + timedelta(seconds=3),
        lease_owner="worker_001",
        execution_attempt=1,
    )

    assert workflow_run.status is WorkflowRunStatus.SUCCEEDED
    assert workflow_run.step_count == 1
    assert workflow_run.started_at == NOW + timedelta(seconds=1)
    assert workflow_run.ended_at == NOW + timedelta(seconds=3)
    assert workflow_run.execution_attempts == 1
    assert workflow_run.lease_owner is None


def test_pending_workflow_can_be_cancelled_without_starting() -> None:
    workflow_run = build_workflow_run()

    workflow_run.cancel(NOW + timedelta(seconds=1))

    assert workflow_run.status is WorkflowRunStatus.CANCELED
    assert workflow_run.started_at is None
    assert workflow_run.ended_at == NOW + timedelta(seconds=1)


def test_pending_workflow_can_be_cancelled_with_audit_metadata() -> None:
    """管理端取消应写入完整可重放审计事实。"""
    workflow_run = build_workflow_run()
    canceled_at = NOW + timedelta(seconds=1)

    workflow_run.cancel(
        canceled_at,
        canceled_by="admin_001",
        reason="Started by mistake.",
        idempotency_key_hash="c" * 64,
        request_hash="d" * 64,
        trace_id="trc_cancel_001",
    )

    assert workflow_run.status is WorkflowRunStatus.CANCELED
    assert workflow_run.ended_at == canceled_at
    assert workflow_run.canceled_at == canceled_at
    assert workflow_run.canceled_by == "admin_001"
    assert workflow_run.cancellation_reason == "Started by mistake."


def test_cancellation_rejects_partial_metadata() -> None:
    """取消审计字段必须全有或全无，避免无法重放的半套事实。"""
    workflow_run = build_workflow_run()

    with pytest.raises(AppValidationError, match="complete"):
        workflow_run.cancel(
            NOW + timedelta(seconds=1),
            canceled_by="admin_001",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": "tenant\n001"},
        {"tenant_id": "tenant\x7f001"},
        {"operator_id": "operator\t001"},
        {"trace_id": "trc\r001"},
    ],
)
def test_workflow_run_rejects_control_characters(
    overrides: dict[str, object],
) -> None:
    """索引、身份和链路字段不能携带会污染日志的控制字符。"""
    with pytest.raises(AppValidationError, match="control characters"):
        build_workflow_run(**overrides)


def test_execution_lease_and_cancellation_reject_control_characters() -> None:
    """运行期租约和取消审计同样要保持单行可审计文本。"""
    workflow_run = build_workflow_run()

    with pytest.raises(AppValidationError, match="control characters"):
        workflow_run.start(
            NOW + timedelta(seconds=1),
            lease_owner="worker\x7f001",
            lease_expires_at=NOW + timedelta(minutes=2),
        )

    with pytest.raises(AppValidationError, match="control characters"):
        workflow_run.cancel(
            NOW + timedelta(seconds=1),
            canceled_by="admin_001",
            reason="Operator canceled\x7fforged line.",
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            trace_id="trc_cancel_001",
        )


def test_terminal_workflow_accepts_valid_audit_purge_watermark() -> None:
    """清理水位必须晚于工作流结束时间。"""
    ended_at = NOW + timedelta(seconds=2)
    workflow_run = build_workflow_run(
        status=WorkflowRunStatus.SUCCEEDED,
        started_at=NOW + timedelta(seconds=1),
        ended_at=ended_at,
        updated_at=ended_at,
        execution_attempts=1,
        audit_purged_at=ended_at + timedelta(days=30),
    )

    assert workflow_run.audit_purged_at == ended_at + timedelta(days=30)


@pytest.mark.parametrize(
    "overrides",
    [
        {"idempotency_key_hash": "not-a-hash"},
        {"request_hash": "A" * 64},
        {"status": "PENDING"},
        {"step_count": -1},
        {"execution_attempts": -1},
        {
            "status": WorkflowRunStatus.RUNNING,
            "started_at": NOW,
            "lease_owner": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "execution_attempts": 1,
        },
        {
            "status": WorkflowRunStatus.SUCCEEDED,
            "started_at": NOW,
            "ended_at": None,
        },
        {"audit_purged_at": NOW + timedelta(days=30)},
        {
            "status": WorkflowRunStatus.CANCELED,
            "ended_at": NOW + timedelta(seconds=1),
            "updated_at": NOW + timedelta(seconds=1),
            "canceled_by": "admin_001",
        },
        {
            "status": WorkflowRunStatus.SUCCEEDED,
            "started_at": NOW,
            "ended_at": NOW + timedelta(seconds=2),
            "updated_at": NOW + timedelta(seconds=2),
            "execution_attempts": 1,
            "audit_purged_at": NOW + timedelta(seconds=1),
        },
    ],
)
def test_workflow_run_rejects_invalid_invariants(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(AppValidationError):
        build_workflow_run(**overrides)


def test_workflow_run_rejects_invalid_transition_and_time_regression() -> None:
    workflow_run = build_workflow_run()

    with pytest.raises(ConflictError, match="not running"):
        workflow_run.mark_failed(
            NOW + timedelta(seconds=1),
            lease_owner="worker_001",
            execution_attempt=1,
        )
    with pytest.raises(AppValidationError, match="must not move backwards"):
        workflow_run.start(
            NOW - timedelta(seconds=1),
            lease_owner="worker_001",
            lease_expires_at=NOW + timedelta(minutes=2),
        )


def test_workflow_run_rejects_non_future_execution_lease() -> None:
    """执行租约必须晚于抢占时间，避免刚写入就被其他实例接管。"""
    workflow_run = build_workflow_run()

    with pytest.raises(AppValidationError, match="later than now"):
        workflow_run.start(
            NOW + timedelta(seconds=1),
            lease_owner="worker_001",
            lease_expires_at=NOW + timedelta(seconds=1),
        )


def test_current_owner_can_extend_execution_lease() -> None:
    """当前所有者可以在租约有效期内推进心跳和截止时间。"""
    workflow_run = build_workflow_run()
    workflow_run.start(
        NOW + timedelta(seconds=1),
        lease_owner="worker_001",
        lease_expires_at=NOW + timedelta(minutes=2),
    )

    workflow_run.heartbeat(
        NOW + timedelta(minutes=1),
        lease_owner="worker_001",
        lease_expires_at=NOW + timedelta(minutes=3),
    )

    assert workflow_run.heartbeat_at == NOW + timedelta(minutes=1)
    assert workflow_run.updated_at == NOW + timedelta(minutes=1)
    assert workflow_run.lease_expires_at == NOW + timedelta(minutes=3)
    assert workflow_run.execution_attempts == 1


def test_heartbeat_rejects_wrong_owner_expiry_and_non_extension() -> None:
    """失权、过期或未延长截止时间的心跳都必须失败。"""
    workflow_run = build_workflow_run()
    workflow_run.start(
        NOW + timedelta(seconds=1),
        lease_owner="worker_001",
        lease_expires_at=NOW + timedelta(minutes=2),
    )

    with pytest.raises(ConflictError, match="owner changed"):
        workflow_run.heartbeat(
            NOW + timedelta(minutes=1),
            lease_owner="worker_002",
            lease_expires_at=NOW + timedelta(minutes=3),
        )
    with pytest.raises(AppValidationError, match="must extend"):
        workflow_run.heartbeat(
            NOW + timedelta(minutes=1),
            lease_owner="worker_001",
            lease_expires_at=NOW + timedelta(minutes=2),
        )
    with pytest.raises(ConflictError, match="expired"):
        workflow_run.heartbeat(
            NOW + timedelta(minutes=2),
            lease_owner="worker_001",
            lease_expires_at=NOW + timedelta(minutes=4),
        )


def test_completion_requires_current_owner_attempt_and_active_lease() -> None:
    """终态提交必须通过所有者、执行代次和有效期三重校验。"""
    workflow_run = build_workflow_run()
    workflow_run.start(
        NOW + timedelta(seconds=1),
        lease_owner="worker_001",
        lease_expires_at=NOW + timedelta(minutes=2),
    )

    with pytest.raises(ConflictError, match="owner changed"):
        workflow_run.mark_failed(
            NOW + timedelta(minutes=1),
            lease_owner="worker_002",
            execution_attempt=1,
        )
    with pytest.raises(ConflictError, match="attempt changed"):
        workflow_run.mark_failed(
            NOW + timedelta(minutes=1),
            lease_owner="worker_001",
            execution_attempt=2,
        )
    with pytest.raises(ConflictError, match="expired"):
        workflow_run.mark_failed(
            NOW + timedelta(minutes=2),
            lease_owner="worker_001",
            execution_attempt=1,
        )

    assert workflow_run.status is WorkflowRunStatus.RUNNING
