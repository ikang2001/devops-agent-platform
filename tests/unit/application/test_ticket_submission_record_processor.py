import json
from datetime import UTC, datetime
from typing import Any

import pytest

from devops_agent_platform.application.exceptions import (
    PersistenceError,
    RuntimeUnavailableError,
    TicketingGatewayError,
)
from devops_agent_platform.application.messages.ticket_submission_requested import (
    TicketSubmissionRequestedEventV1,
)
from devops_agent_platform.application.services import (
    ticket_submission_record_processor as record_processor,
)
from devops_agent_platform.application.services import (
    ticket_submission_requested_handler as submission_handler,
)
from devops_agent_platform.domain.enums import TicketSubmissionStatus
from devops_agent_platform.domain.exceptions import ResourceNotFound

NOW = datetime(2026, 7, 1, 19, 0, tzinfo=UTC)


def build_envelope() -> dict[str, Any]:
    """构造原始记录处理测试使用的有效消息。"""
    return {
        "event_id": "evt_ticket_submission_requested_001",
        "event_type": "ticket_submission.requested",
        "schema_version": 1,
        "tenant_id": "tenant_001",
        "aggregate_type": "TicketSubmission",
        "aggregate_id": "tsb_001",
        "occurred_at": NOW.isoformat(),
        "trace_id": "trc_ticket_submission_001",
        "payload": {
            "ticket_submission_id": "tsb_001",
            "ticket_draft_id": "tdf_001",
            "workflow_run_id": "wfr_001",
            "target_system": "jira",
            "draft_version": 2,
            "requested_by": "submitter_001",
            "requested_at": NOW.isoformat(),
        },
    }


def encode_envelope(envelope: object | None = None) -> bytes:
    """序列化消息，默认使用有效 Envelope。"""
    return json.dumps(
        build_envelope() if envelope is None else envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


class FakeTicketSubmissionHandler:
    """返回指定处理结果或异常的消息处理器。"""

    def __init__(
        self,
        *,
        disposition: submission_handler.TicketSubmissionRequestedDisposition = (
            submission_handler.TicketSubmissionRequestedDisposition.SUBMITTED
        ),
        error: Exception | None = None,
    ) -> None:
        self._disposition = disposition
        self._error = error
        self.events: list[TicketSubmissionRequestedEventV1] = []

    async def handle(
        self,
        event: TicketSubmissionRequestedEventV1,
    ) -> submission_handler.TicketSubmissionRequestedHandlingResult:
        """记录已解析事件并返回预设行为。"""
        self.events.append(event)
        if self._error is not None:
            raise self._error
        status = (
            TicketSubmissionStatus.SUBMITTED.value
            if self._disposition
            is submission_handler.TicketSubmissionRequestedDisposition.SUBMITTED
            else TicketSubmissionStatus.FAILED.value
        )
        return submission_handler.TicketSubmissionRequestedHandlingResult(
            event_id=event.event_id,
            ticket_submission_id=event.ticket_submission_id,
            disposition=self._disposition,
            submission_status=status,
            trace_id=event.trace_id,
            external_ticket_id="JIRA-101"
            if status == TicketSubmissionStatus.SUBMITTED.value
            else None,
            failure_reason="provider failed"
            if status == TicketSubmissionStatus.FAILED.value
            else None,
        )


@pytest.mark.parametrize(
    "handler_disposition",
    [
        submission_handler.TicketSubmissionRequestedDisposition.SUBMITTED,
        submission_handler.TicketSubmissionRequestedDisposition.FAILED,
        submission_handler.TicketSubmissionRequestedDisposition.IGNORED,
    ],
)
async def test_valid_or_duplicate_submission_message_is_acknowledged(
    handler_disposition: (submission_handler.TicketSubmissionRequestedDisposition),
) -> None:
    """提交成功、失败记录和幂等忽略都可以提交 offset。"""
    handler = FakeTicketSubmissionHandler(disposition=handler_disposition)
    processor = record_processor.TicketSubmissionRequestedRecordProcessor(handler)

    result = await processor.process(encode_envelope())

    assert result.disposition is record_processor.TicketSubmissionMessageDisposition.ACK
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
async def test_invalid_raw_submission_message_is_dead_lettered(
    value: bytes,
    reason_code: str,
) -> None:
    """无法解析或超限的原始消息重试不会恢复，应进入死信。"""
    result = await record_processor.TicketSubmissionRequestedRecordProcessor(
        FakeTicketSubmissionHandler()
    ).process(value)

    assert (
        result.disposition
        is record_processor.TicketSubmissionMessageDisposition.DEAD_LETTER
    )
    assert result.reason_code == reason_code


async def test_submission_contract_error_is_dead_lettered() -> None:
    """已解析 JSON 但契约版本不支持时进入死信。"""
    envelope = build_envelope()
    envelope["schema_version"] = 99

    result = await record_processor.TicketSubmissionRequestedRecordProcessor(
        FakeTicketSubmissionHandler()
    ).process(encode_envelope(envelope))

    assert (
        result.disposition
        is record_processor.TicketSubmissionMessageDisposition.DEAD_LETTER
    )
    assert result.reason_code == "MESSAGE_CONTRACT_INVALID"


async def test_dirty_submission_event_type_is_not_acknowledged_as_foreign() -> None:
    """污染的 event_type 不能借共享 Topic 旁路被确认掉。"""
    envelope = build_envelope()
    envelope["event_type"] = "rca.requested\x7fforged"

    result = await record_processor.TicketSubmissionRequestedRecordProcessor(
        FakeTicketSubmissionHandler()
    ).process(encode_envelope(envelope))

    assert (
        result.disposition
        is record_processor.TicketSubmissionMessageDisposition.DEAD_LETTER
    )
    assert result.reason_code == "MESSAGE_CONTRACT_INVALID"


async def test_valid_non_submission_event_is_acknowledged() -> None:
    """共享 Topic 中的其他业务事件不属于坏消息，应跳过并确认。"""
    envelope = build_envelope()
    envelope["event_type"] = "rca.requested"
    handler = FakeTicketSubmissionHandler()
    processor = record_processor.TicketSubmissionRequestedRecordProcessor(handler)

    result = await processor.process(encode_envelope(envelope))

    assert result.disposition is record_processor.TicketSubmissionMessageDisposition.ACK
    assert result.reason_code == "EVENT_NOT_TARGETED"
    assert handler.events == []


async def test_missing_submission_message_is_dead_lettered() -> None:
    """Outbox 消息引用不存在本地提交记录属于不可自愈数据异常。"""
    result = await record_processor.TicketSubmissionRequestedRecordProcessor(
        FakeTicketSubmissionHandler(
            error=ResourceNotFound("ticket submission missing"),
        )
    ).process(encode_envelope())

    assert (
        result.disposition
        is record_processor.TicketSubmissionMessageDisposition.DEAD_LETTER
    )
    assert result.reason_code == "TICKET_SUBMISSION_NOT_FOUND"


@pytest.mark.parametrize(
    ("error", "reason_code"),
    [
        (PersistenceError("database unavailable"), "PERSISTENCE_ERROR"),
        (RuntimeUnavailableError(), "RUNTIME_UNAVAILABLE"),
        (
            TicketingGatewayError("ticketing timeout"),
            "TICKETING_GATEWAY_UNAVAILABLE",
        ),
        (RuntimeError("gateway unavailable"), "UNEXPECTED_PROCESSING_ERROR"),
    ],
)
async def test_transient_or_unknown_submission_error_is_retried(
    error: Exception,
    reason_code: str,
) -> None:
    """基础设施、外部系统和未知故障优先保留消息，交给外层退避重试。"""
    result = await record_processor.TicketSubmissionRequestedRecordProcessor(
        FakeTicketSubmissionHandler(error=error)
    ).process(encode_envelope())

    assert (
        result.disposition is record_processor.TicketSubmissionMessageDisposition.RETRY
    )
    assert result.reason_code == reason_code
    assert result.reason is not None
    assert "\n" not in result.reason


async def test_submission_retry_reason_redacts_sensitive_error_detail() -> None:
    """工单提交重试原因不得携带外部网关异常中的敏感片段。"""
    result = await record_processor.TicketSubmissionRequestedRecordProcessor(
        FakeTicketSubmissionHandler(
            error=RuntimeError("gateway password=hunter2 client_secret=secret-token")
        )
    ).process(encode_envelope())

    assert (
        result.disposition is record_processor.TicketSubmissionMessageDisposition.RETRY
    )
    assert result.reason is not None
    assert "hunter2" not in result.reason
    assert "secret-token" not in result.reason
    assert "password=[REDACTED]" in result.reason
    assert "client_secret=[REDACTED]" in result.reason
