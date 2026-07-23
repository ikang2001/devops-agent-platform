import json
from datetime import UTC, datetime
from typing import Any

import pytest

from devops_agent_platform.application.exceptions import (
    PersistenceError,
    ResourceBusyError,
    RuntimeUnavailableError,
)
from devops_agent_platform.application.messages.rca_requested import (
    RCARequestedEventV1,
)
from devops_agent_platform.application.services.rca_record_processor import (
    MessageDisposition,
    RCARequestedRecordProcessor,
)
from devops_agent_platform.application.services.rca_requested_handler import (
    RCARequestedDisposition,
    RCARequestedHandlingResult,
)
from devops_agent_platform.domain.exceptions import ResourceNotFound

NOW = datetime(2026, 6, 28, 17, 0, tzinfo=UTC)


def build_envelope() -> dict[str, Any]:
    """构造原始记录处理测试使用的有效消息。"""
    return {
        "event_id": "evt_rca_001",
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


def encode_envelope(envelope: object | None = None) -> bytes:
    """序列化消息，默认使用有效Envelope。"""
    return json.dumps(
        build_envelope() if envelope is None else envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


class FakeRCARequestedHandler:
    """返回指定处理结果或异常的消息处理器。"""

    def __init__(
        self,
        *,
        disposition: RCARequestedDisposition = (RCARequestedDisposition.EXECUTED),
        error: Exception | None = None,
    ) -> None:
        self._disposition = disposition
        self._error = error
        self.events: list[RCARequestedEventV1] = []

    async def handle(
        self,
        event: RCARequestedEventV1,
    ) -> RCARequestedHandlingResult:
        """记录已解析事件并返回预设行为。"""
        self.events.append(event)
        if self._error is not None:
            raise self._error
        return RCARequestedHandlingResult(
            event_id=event.event_id,
            workflow_run_id=event.workflow_run_id,
            disposition=self._disposition,
            workflow_status="RUNNING",
            execution_attempt=1,
            trace_id=event.trace_id,
        )


@pytest.mark.parametrize(
    "handler_disposition",
    [
        RCARequestedDisposition.EXECUTED,
        RCARequestedDisposition.IGNORED,
    ],
)
async def test_valid_or_duplicate_message_is_acknowledged(
    handler_disposition: RCARequestedDisposition,
) -> None:
    """成功抢占和幂等忽略都已经完成业务处置，可以提交offset。"""
    handler = FakeRCARequestedHandler(disposition=handler_disposition)
    processor = RCARequestedRecordProcessor(handler)

    result = await processor.process(encode_envelope())

    assert result.disposition is MessageDisposition.ACK
    assert result.handling is not None
    assert result.handling.disposition is handler_disposition
    assert len(handler.events) == 1


@pytest.mark.parametrize(
    ("value", "reason_code"),
    [
        (b"", "INVALID_MESSAGE_SIZE"),
        (b"\xff", "INVALID_JSON"),
        (b"{invalid", "INVALID_JSON"),
        (b"x" * (1024 * 1024 + 1), "INVALID_MESSAGE_SIZE"),
    ],
    ids=[
        "empty",
        "invalid-utf8",
        "malformed-json",
        "oversized",
    ],
)
async def test_invalid_raw_message_is_sent_to_dead_letter(
    value: bytes,
    reason_code: str,
) -> None:
    """无法解析或超限的原始消息重试不会恢复，应进入死信。"""
    result = await RCARequestedRecordProcessor(FakeRCARequestedHandler()).process(value)

    assert result.disposition is MessageDisposition.DEAD_LETTER
    assert result.reason_code == reason_code


async def test_contract_error_is_sent_to_dead_letter() -> None:
    """已解析JSON但契约版本不支持时进入死信。"""
    envelope = build_envelope()
    envelope["schema_version"] = 99

    result = await RCARequestedRecordProcessor(FakeRCARequestedHandler()).process(
        encode_envelope(envelope)
    )

    assert result.disposition is MessageDisposition.DEAD_LETTER
    assert result.reason_code == "MESSAGE_CONTRACT_INVALID"


async def test_dirty_event_type_is_not_acknowledged_as_foreign() -> None:
    """污染的 event_type 不能借共享 Topic 旁路被确认掉。"""
    envelope = build_envelope()
    envelope["event_type"] = "incident.created\x7fforged"

    result = await RCARequestedRecordProcessor(FakeRCARequestedHandler()).process(
        encode_envelope(envelope)
    )

    assert result.disposition is MessageDisposition.DEAD_LETTER
    assert result.reason_code == "MESSAGE_CONTRACT_INVALID"


async def test_valid_non_rca_event_on_shared_topic_is_acknowledged() -> None:
    """共享Topic中的其他业务事件不属于坏消息，应直接跳过并确认。"""
    envelope = build_envelope()
    envelope["event_type"] = "incident.created"
    handler = FakeRCARequestedHandler()

    result = await RCARequestedRecordProcessor(handler).process(
        encode_envelope(envelope)
    )

    assert result.disposition is MessageDisposition.ACK
    assert result.reason_code == "EVENT_NOT_TARGETED"
    assert handler.events == []


async def test_missing_workflow_is_sent_to_dead_letter() -> None:
    """Outbox消息引用不存在工作流属于不可自愈数据异常。"""
    result = await RCARequestedRecordProcessor(
        FakeRCARequestedHandler(
            error=ResourceNotFound("workflow missing"),
        )
    ).process(encode_envelope())

    assert result.disposition is MessageDisposition.DEAD_LETTER
    assert result.reason_code == "WORKFLOW_NOT_FOUND"


@pytest.mark.parametrize(
    ("error", "reason_code"),
    [
        (PersistenceError("database unavailable"), "PERSISTENCE_ERROR"),
        (ResourceBusyError("workflow active"), "RESOURCE_BUSY"),
        (RuntimeUnavailableError(), "RUNTIME_UNAVAILABLE"),
        (RuntimeError("unexpected bug"), "UNEXPECTED_PROCESSING_ERROR"),
    ],
)
async def test_transient_or_unknown_error_is_retried(
    error: Exception,
    reason_code: str,
) -> None:
    """基础设施和未知故障优先保留消息，交给外层退避重试。"""
    result = await RCARequestedRecordProcessor(
        FakeRCARequestedHandler(error=error)
    ).process(encode_envelope())

    assert result.disposition is MessageDisposition.RETRY
    assert result.reason_code == reason_code
    assert result.reason is not None
    assert "\n" not in result.reason


async def test_retry_reason_redacts_sensitive_error_detail() -> None:
    """重试原因会进入健康状态和日志，不能携带异常中的凭据片段。"""
    result = await RCARequestedRecordProcessor(
        FakeRCARequestedHandler(
            error=RuntimeError("prometheus password=hunter2 token=secret-token")
        )
    ).process(encode_envelope())

    assert result.disposition is MessageDisposition.RETRY
    assert result.reason is not None
    assert "hunter2" not in result.reason
    assert "secret-token" not in result.reason
    assert "password=[REDACTED]" in result.reason
    assert "token=[REDACTED]" in result.reason
