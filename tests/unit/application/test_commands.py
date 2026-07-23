from datetime import UTC, datetime

import pytest

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.application.commands.rca import StartRCACommand
from devops_agent_platform.application.commands.workflow_execution import (
    ClaimWorkflowRunCommand,
    CompleteWorkflowRunCommand,
    ExecuteRCAWorkflowCommand,
    HeartbeatWorkflowRunCommand,
)
from devops_agent_platform.domain.enums import AlertSeverity, WorkflowRunStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.interfaces.http.dto import AlertWebhookRequest


def test_receive_alert_command_has_no_fastapi_dependency() -> None:
    command = ReceiveAlertCommand(
        tenant_id="tenant-a",
        source="alertmanager",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        summary="5xx error rate is high",
        starts_at=datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        fingerprint="fp-001",
        external_event_id="evt-001",
        trace_id="trc_001",
    )

    assert command.trace_id == "trc_001"
    assert command.severity is AlertSeverity.CRITICAL
    assert "fastapi" not in ReceiveAlertCommand.__module__


def test_receive_alert_command_allows_multiline_summary_for_sanitizer() -> None:
    """summary 可保留换行给应用服务压单行，但 Command 仍独立校验。"""
    command = ReceiveAlertCommand(
        tenant_id="tenant-a",
        source="alertmanager",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        summary="password=hunter2 copied\nneeds review",
        starts_at=datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        fingerprint="fp-001",
        external_event_id="evt-001",
        trace_id="trc_001",
    )

    assert "\n" in command.summary


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": " tenant-a"},
        {"source": "alertmanager\x7fforged"},
        {"source": "alert manager"},
        {"service_name": "checkout api"},
        {"fingerprint": ""},
        {"external_event_id": "evt 001"},
        {"trace_id": "trc 001"},
        {"summary": "bad\tcontrol"},
        {"summary": "bad\x7fcontrol"},
        {"summary": " "},
        {"severity": "CRITICAL"},
        {"starts_at": datetime(2026, 6, 27, 10, 0)},
    ],
)
def test_receive_alert_command_rejects_invalid_input(
    changes: dict[str, object],
) -> None:
    """应用层 Command 不能依赖 HTTP DTO 才能挡住脏输入。"""
    values = {
        "tenant_id": "tenant-a",
        "source": "alertmanager",
        "service_name": "checkout-api",
        "severity": AlertSeverity.CRITICAL,
        "summary": "5xx error rate is high",
        "starts_at": datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        "fingerprint": "fp-001",
        "external_event_id": "evt-001",
        "trace_id": "trc_001",
    }
    values.update(changes)

    with pytest.raises(AppValidationError):
        ReceiveAlertCommand(**values)  # type: ignore[arg-type]


def test_http_dto_can_convert_to_application_command() -> None:
    payload = AlertWebhookRequest(
        tenant_id="tenant-a",
        source="alertmanager",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        summary="5xx error rate is high",
        starts_at=datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        fingerprint="fp-001",
        external_event_id="evt-001",
    )

    command = payload.to_command(trace_id="trc_001")

    assert isinstance(command, ReceiveAlertCommand)
    assert command.tenant_id == "tenant-a"
    assert command.external_event_id == "evt-001"
    assert command.trace_id == "trc_001"


def test_rca_command_has_no_transport_dependency() -> None:
    """RCA Command 保持框架无关，身份由认证路由注入。"""
    command = StartRCACommand(
        incident_id="inc-001",
        tenant_id="tenant-a",
        operator_id="operator-001",
        idempotency_key="idem-001",
        trace_id="trc-001",
    )

    assert isinstance(command, StartRCACommand)
    assert command.incident_id == "inc-001"
    assert command.idempotency_key == "idem-001"
    assert "fastapi" not in StartRCACommand.__module__


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("incident_id", "inc\n001"),
        ("tenant_id", "tenant\t001"),
        ("operator_id", "operator\r001"),
        ("idempotency_key", "idem\n001"),
        ("trace_id", "trc\x7f001"),
        ("trace_id", "trc\n001"),
    ],
)
def test_start_rca_command_rejects_control_characters(
    field_name: str,
    value: str,
) -> None:
    """RCA 启动命令不能把控制字符带入工作流审计链路。"""
    kwargs = {
        "incident_id": "inc-001",
        "tenant_id": "tenant-a",
        "operator_id": "operator-001",
        "idempotency_key": "idem-001",
        "trace_id": "trc-001",
    }
    kwargs[field_name] = value

    with pytest.raises(AppValidationError, match="control characters"):
        StartRCACommand(**kwargs)


@pytest.mark.parametrize(
    "changes",
    [
        {"trace_id": "trc_cancel\x7fforged"},
        {"requested_by": "admin\x7fforged"},
        {"reason": "Operator canceled\x7fforged"},
    ],
)
def test_cancel_rca_workflow_command_rejects_del_control_character(
    changes: dict[str, object],
) -> None:
    """取消命令的身份和原因字段不能携带DEL污染审计事实。"""
    from devops_agent_platform.application.commands.rca import (
        CancelRCAWorkflowCommand,
    )

    kwargs = {
        "tenant_id": "tenant-a",
        "workflow_run_id": "wfr-001",
        "expected_version": 1,
        "reason": "Operator canceled stale RCA.",
        "idempotency_key": "idem-cancel-001",
        "requested_by": "admin-001",
        "trace_id": "trc-cancel-001",
    }
    kwargs.update(changes)

    with pytest.raises(AppValidationError):
        CancelRCAWorkflowCommand(**kwargs)  # type: ignore[arg-type]


def test_workflow_claim_command_has_no_transport_dependency() -> None:
    """消费适配器也必须转换为不依赖Kafka SDK的应用命令。"""
    command = ClaimWorkflowRunCommand(
        tenant_id="tenant-a",
        workflow_run_id="wfr-001",
        worker_id="worker-001",
    )

    assert command.worker_id == "worker-001"
    assert "aiokafka" not in ClaimWorkflowRunCommand.__module__

    heartbeat = HeartbeatWorkflowRunCommand(
        tenant_id="tenant-a",
        workflow_run_id="wfr-001",
        worker_id="worker-001",
    )
    assert heartbeat.workflow_run_id == "wfr-001"

    completion = CompleteWorkflowRunCommand(
        tenant_id="tenant-a",
        workflow_run_id="wfr-001",
        worker_id="worker-001",
        execution_attempt=1,
        target_status=WorkflowRunStatus.SUCCEEDED,
    )
    assert completion.execution_attempt == 1

    execution = ExecuteRCAWorkflowCommand(
        tenant_id="tenant-a",
        workflow_run_id="wfr-001",
        incident_id="inc-001",
        operator_id="operator-001",
        worker_id="worker-001",
        execution_attempt=1,
        trace_id="trc-001",
    )
    assert execution.incident_id == "inc-001"


@pytest.mark.parametrize(
    "tenant_id",
    ["tenant\nforged", "tenant\tforged", "tenant\x7fforged"],
)
def test_workflow_execution_commands_reject_control_character_tenant_id(
    tenant_id: str,
) -> None:
    """执行命令的租户键必须先在应用边界保持单行。"""
    with pytest.raises(AppValidationError, match="control characters"):
        ClaimWorkflowRunCommand(
            tenant_id=tenant_id,
            workflow_run_id="wfr-001",
            worker_id="worker-001",
        )
    with pytest.raises(AppValidationError, match="control characters"):
        HeartbeatWorkflowRunCommand(
            tenant_id=tenant_id,
            workflow_run_id="wfr-001",
            worker_id="worker-001",
        )
    with pytest.raises(AppValidationError, match="control characters"):
        CompleteWorkflowRunCommand(
            tenant_id=tenant_id,
            workflow_run_id="wfr-001",
            worker_id="worker-001",
            execution_attempt=1,
            target_status=WorkflowRunStatus.FAILED,
        )
    with pytest.raises(AppValidationError, match="control characters"):
        ExecuteRCAWorkflowCommand(
            tenant_id=tenant_id,
            workflow_run_id="wfr-001",
            incident_id="inc-001",
            operator_id="operator-001",
            worker_id="worker-001",
            execution_attempt=1,
            trace_id="trc-001",
        )


@pytest.mark.parametrize(
    "worker_id",
    [" worker-001", "worker\nforged", "w" * 129],
)
def test_workflow_execution_commands_reject_invalid_worker_id(
    worker_id: str,
) -> None:
    """工作流租约命令层也必须复用统一Worker身份规则。"""
    with pytest.raises(AppValidationError, match="worker_id"):
        ClaimWorkflowRunCommand(
            tenant_id="tenant-a",
            workflow_run_id="wfr-001",
            worker_id=worker_id,
        )
    with pytest.raises(AppValidationError, match="worker_id"):
        HeartbeatWorkflowRunCommand(
            tenant_id="tenant-a",
            workflow_run_id="wfr-001",
            worker_id=worker_id,
        )
    with pytest.raises(AppValidationError, match="worker_id"):
        CompleteWorkflowRunCommand(
            tenant_id="tenant-a",
            workflow_run_id="wfr-001",
            worker_id=worker_id,
            execution_attempt=1,
            target_status=WorkflowRunStatus.FAILED,
        )
    with pytest.raises(AppValidationError, match="worker_id"):
        ExecuteRCAWorkflowCommand(
            tenant_id="tenant-a",
            workflow_run_id="wfr-001",
            incident_id="inc-001",
            operator_id="operator-001",
            worker_id=worker_id,
            execution_attempt=1,
            trace_id="trc-001",
        )
