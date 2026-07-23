from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from devops_agent_platform.application.commands.workflow_execution import (
    ClaimWorkflowRunCommand,
)
from devops_agent_platform.application.exceptions import (
    MessageContractError,
    PersistenceError,
    ResourceBusyError,
)
from devops_agent_platform.application.messages.rca_requested import (
    RCARequestedEventV1,
)
from devops_agent_platform.application.services.rca_execution_coordinator import (
    RCAExecutionResult,
)
from devops_agent_platform.application.services.rca_requested_handler import (
    RCARequestedDisposition,
    RCARequestedMessageHandler,
)
from devops_agent_platform.application.services.workflow_execution_service import (
    WorkflowClaimResult,
)
from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)

NOW = datetime(2026, 6, 28, 16, 0, tzinfo=UTC)


def build_envelope() -> dict[str, Any]:
    """构造与Kafka Publisher输出一致的rca.requested v1 Envelope。"""
    return {
        "event_id": "evt_rca_requested_001",
        "event_type": "rca.requested",
        "schema_version": 1,
        "tenant_id": "tenant_001",
        "aggregate_type": "WorkflowRun",
        "aggregate_id": "wfr_001",
        "occurred_at": NOW.isoformat(),
        "trace_id": "trc_001",
        "payload": {
            "workflow_run_id": "wfr_001",
            "incident_id": "inc_001",
            "tenant_id": "tenant_001",
            "operator_id": "operator_001",
            "requested_at": NOW.isoformat(),
        },
    }


def test_valid_v1_envelope_is_parsed_and_allows_additive_fields() -> None:
    """第一版消费者应容忍附加字段，支持生产者做兼容扩展。"""
    envelope = build_envelope()
    envelope["producer"] = "devops-agent-platform"
    envelope["payload"]["future_optional_field"] = "value"

    event = RCARequestedEventV1.from_envelope(envelope)

    assert event.event_id == "evt_rca_requested_001"
    assert event.workflow_run_id == "wfr_001"
    assert event.incident_id == "inc_001"
    assert event.tenant_id == "tenant_001"
    assert event.occurred_at == NOW
    assert event.requested_at == NOW


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("event_type", "incident.created", "Unsupported event_type"),
        ("schema_version", 2, "schema_version"),
        ("schema_version", True, "schema_version"),
        ("aggregate_type", "Incident", "aggregate_type"),
        ("event_id", "", "event_id"),
        ("occurred_at", "2026-06-28T16:00:00", "timezone"),
    ],
)
def test_invalid_envelope_metadata_is_rejected(
    field_name: str,
    value: object,
    message: str,
) -> None:
    """路由、版本、身份和时间元数据错误属于不可重试坏消息。"""
    envelope = build_envelope()
    envelope[field_name] = value

    with pytest.raises(MessageContractError, match=message):
        RCARequestedEventV1.from_envelope(envelope)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("event_id", "evt_rca_requested_001\nforged"),
        ("tenant_id", "tenant_001\rforged"),
        ("aggregate_id", "wfr_001\tforged"),
        ("trace_id", "trc_001\x7fforged"),
        ("trace_id", "trc_001\nforged"),
    ],
)
def test_envelope_metadata_rejects_control_characters(
    field_name: str,
    value: str,
) -> None:
    """来自 Kafka 的身份字段也必须单行，不能只信任生产者。"""
    envelope = build_envelope()
    envelope[field_name] = value

    with pytest.raises(MessageContractError, match="control characters"):
        RCARequestedEventV1.from_envelope(envelope)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("tenant_id", "tenant_002", "tenant_id do not match"),
        ("workflow_run_id", "wfr_002", "workflow_run_id do not match"),
        ("incident_id", "", "incident_id"),
        ("operator_id", None, "operator_id"),
        ("requested_at", "not-a-time", "ISO timestamp"),
    ],
)
def test_invalid_or_inconsistent_payload_is_rejected(
    field_name: str,
    value: object,
    message: str,
) -> None:
    """Payload字段错误不能进入数据库抢占流程。"""
    envelope = build_envelope()
    envelope["payload"][field_name] = value

    with pytest.raises(MessageContractError, match=message):
        RCARequestedEventV1.from_envelope(envelope)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("workflow_run_id", "wfr_001\nforged"),
        ("incident_id", "inc_001\rforged"),
        ("operator_id", "operator_001\x7fforged"),
        ("operator_id", "operator_001\tforged"),
    ],
)
def test_payload_rejects_control_characters(
    field_name: str,
    value: str,
) -> None:
    """Payload 里的路由身份也不能携带日志污染字符。"""
    envelope = build_envelope()
    envelope["payload"][field_name] = value

    with pytest.raises(MessageContractError, match="control characters"):
        RCARequestedEventV1.from_envelope(envelope)


def test_non_object_envelope_or_payload_is_rejected() -> None:
    """JSON顶层和Payload都必须是对象结构。"""
    with pytest.raises(MessageContractError, match="envelope"):
        RCARequestedEventV1.from_envelope([])  # type: ignore[arg-type]

    envelope = build_envelope()
    envelope["payload"] = []
    with pytest.raises(MessageContractError, match="payload"):
        RCARequestedEventV1.from_envelope(envelope)


class RecordingWorkflowExecutionService:
    """记录处理器生成的抢占命令，并返回可预测结果或异常。"""

    def __init__(
        self,
        *,
        acquired: bool = True,
        status: str = WorkflowRunStatus.RUNNING.value,
        error: Exception | None = None,
    ) -> None:
        self._acquired = acquired
        self._status = status
        self._error = error
        self.commands: list[ClaimWorkflowRunCommand] = []

    async def claim(
        self,
        command: ClaimWorkflowRunCommand,
    ) -> WorkflowClaimResult:
        """模拟执行服务的抢占边界。"""
        self.commands.append(command)
        if self._error is not None:
            raise self._error
        return WorkflowClaimResult(
            workflow_run_id=command.workflow_run_id,
            status=self._status,
            acquired=self._acquired,
            lease_owner=command.worker_id,
            lease_expires_at=NOW + timedelta(minutes=2),
            execution_attempts=1,
            trace_id="trc_db_source",
        )


class RecordingExecutionCoordinator:
    """记录抢占后的Agent协调调用并返回成功终态。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def execute(
        self,
        event: RCARequestedEventV1,
        claim: WorkflowClaimResult,
        worker_id: str,
    ) -> RCAExecutionResult:
        self.calls.append((event.workflow_run_id, claim.execution_attempts, worker_id))
        return RCAExecutionResult(
            workflow_run_id=event.workflow_run_id,
            status=WorkflowRunStatus.SUCCEEDED,
            execution_attempt=claim.execution_attempts,
            timed_out=False,
            agent_error=None,
            trace_id=claim.trace_id,
        )


async def test_handler_executes_claimed_workflow_before_ack() -> None:
    """抢占成功后必须完成Agent协调与终态收口，才能返回EXECUTED。"""
    service = RecordingWorkflowExecutionService()
    coordinator = RecordingExecutionCoordinator()
    handler = RCARequestedMessageHandler(
        workflow_execution_service=service,
        execution_coordinator=coordinator,
        worker_id="rca-consumer-001",
    )
    event = RCARequestedEventV1.from_envelope(build_envelope())

    result = await handler.handle(event)

    assert result.disposition is RCARequestedDisposition.EXECUTED
    assert result.workflow_status == WorkflowRunStatus.SUCCEEDED.value
    assert result.execution_attempt == 1
    assert result.trace_id == "trc_db_source"
    assert coordinator.calls == [("wfr_001", 1, "rca-consumer-001")]
    assert service.commands == [
        ClaimWorkflowRunCommand(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            worker_id="rca-consumer-001",
        )
    ]


async def test_handler_ignores_only_terminal_duplicate() -> None:
    """已落终态的重复消息可以确认，且不再次调用Agent。"""
    service = RecordingWorkflowExecutionService(
        acquired=False,
        status=WorkflowRunStatus.SUCCEEDED.value,
    )
    coordinator = RecordingExecutionCoordinator()
    handler = RCARequestedMessageHandler(
        workflow_execution_service=service,
        execution_coordinator=coordinator,
        worker_id="rca-consumer-001",
    )

    result = await handler.handle(RCARequestedEventV1.from_envelope(build_envelope()))

    assert result.disposition is RCARequestedDisposition.IGNORED
    assert coordinator.calls == []


async def test_handler_acknowledges_canceled_duplicate_without_agent() -> None:
    """取消后的旧消息必须确认掉，避免队列因控制面取消而反复重试。"""
    service = RecordingWorkflowExecutionService(
        acquired=False,
        status=WorkflowRunStatus.CANCELED.value,
    )
    coordinator = RecordingExecutionCoordinator()
    handler = RCARequestedMessageHandler(
        workflow_execution_service=service,
        execution_coordinator=coordinator,
        worker_id="rca-consumer-001",
    )

    result = await handler.handle(RCARequestedEventV1.from_envelope(build_envelope()))

    assert result.disposition is RCARequestedDisposition.IGNORED
    assert result.workflow_status == WorkflowRunStatus.CANCELED.value
    assert coordinator.calls == []


async def test_handler_retries_active_duplicate() -> None:
    """活动状态的重复消息必须保留，等待终态或租约接管。"""
    handler = RCARequestedMessageHandler(
        workflow_execution_service=RecordingWorkflowExecutionService(
            acquired=False,
            status=WorkflowRunStatus.RUNNING.value,
        ),
        execution_coordinator=RecordingExecutionCoordinator(),
        worker_id="rca-consumer-001",
    )

    with pytest.raises(ResourceBusyError):
        await handler.handle(RCARequestedEventV1.from_envelope(build_envelope()))


@pytest.mark.parametrize(
    "error",
    [
        PersistenceError("database unavailable"),
        ResourceNotFound("workflow missing"),
    ],
)
async def test_handler_does_not_swallow_retry_or_data_errors(
    error: Exception,
) -> None:
    """处理器保留异常类型，由外层消费策略决定重试或死信。"""
    service = RecordingWorkflowExecutionService(error=error)
    handler = RCARequestedMessageHandler(
        workflow_execution_service=service,
        execution_coordinator=RecordingExecutionCoordinator(),
        worker_id="rca-consumer-001",
    )

    with pytest.raises(type(error), match=str(error)):
        await handler.handle(RCARequestedEventV1.from_envelope(build_envelope()))


@pytest.mark.parametrize(
    "worker_id",
    [" ", "rca\nforged", "w" * 129],
)
def test_handler_rejects_invalid_worker_id_during_bootstrap(
    worker_id: str,
) -> None:
    """错误消费者标识应在启动装配时暴露，而不是处理消息后才失败。"""
    service = RecordingWorkflowExecutionService()

    with pytest.raises(AppValidationError, match="worker_id"):
        RCARequestedMessageHandler(
            workflow_execution_service=service,
            execution_coordinator=RecordingExecutionCoordinator(),
            worker_id=worker_id,
        )
