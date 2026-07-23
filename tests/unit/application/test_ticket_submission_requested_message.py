from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from devops_agent_platform.application.commands.ticket_drafts import (
    CompleteTicketSubmissionCommand,
)
from devops_agent_platform.application.exceptions import MessageContractError
from devops_agent_platform.application.messages.ticket_submission_requested import (
    TicketSubmissionRequestedEventV1,
)
from devops_agent_platform.application.services import (
    ticket_submission_requested_handler as submission_handler,
)
from devops_agent_platform.application.services.ticket_submission_service import (
    TicketSubmissionView,
)
from devops_agent_platform.domain.enums import (
    TicketDraftStatus,
    TicketPriority,
    TicketSubmissionStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.ticket_draft import TicketDraft
from devops_agent_platform.domain.models.ticket_submission import TicketSubmission
from devops_agent_platform.ports.ticketing import (
    TicketingSubmitOutcome,
    TicketingSubmitRequest,
)

NOW = datetime(2026, 7, 1, 18, 0, tzinfo=UTC)
HASH = "a" * 64
_DEFAULT = object()


def build_envelope() -> dict[str, Any]:
    """构造 ticket_submission.requested v1 有效 Envelope。"""
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


def build_draft(
    *,
    status: TicketDraftStatus = TicketDraftStatus.APPROVED,
    version: int = 2,
) -> TicketDraft:
    """构造已审批 Ticket Draft 快照。"""
    return TicketDraft(
        ticket_draft_id="tdf_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        report_id="rpt_001",
        status=status,
        priority=TicketPriority.P1,
        title="Checkout outage",
        description="RCA conclusion: dependency timeout",
        evidence_ids=("evd_001",),
        recommendations=("Open vendor incident.",),
        created_by="admin_001",
        idempotency_key_hash=HASH,
        request_hash=HASH,
        trace_id="trc_create",
        created_at=NOW - timedelta(minutes=10),
        version=version,
        decided_by="approver_001" if version == 2 else None,
        decided_at=NOW - timedelta(minutes=5) if version == 2 else None,
        decision_idempotency_key_hash=HASH if version == 2 else None,
        decision_request_hash=HASH if version == 2 else None,
        decision_trace_id="trc_decision" if version == 2 else None,
    )


def build_submission(
    *,
    status: TicketSubmissionStatus = TicketSubmissionStatus.REQUESTED,
) -> TicketSubmission:
    """构造提交请求或终态提交记录。"""
    common = {
        "ticket_submission_id": "tsb_001",
        "tenant_id": "tenant_001",
        "ticket_draft_id": "tdf_001",
        "workflow_run_id": "wfr_001",
        "target_system": "jira",
        "idempotency_key_hash": HASH,
        "request_hash": HASH,
        "requested_by": "submitter_001",
        "trace_id": "trc_ticket_submission_001",
        "requested_at": NOW,
    }
    if status is TicketSubmissionStatus.REQUESTED:
        return TicketSubmission(status=status, version=1, **common)
    return TicketSubmission(
        status=status,
        version=2,
        external_ticket_id="JIRA-101"
        if status is TicketSubmissionStatus.SUBMITTED
        else None,
        external_ticket_url="https://jira.example/browse/JIRA-101"
        if status is TicketSubmissionStatus.SUBMITTED
        else None,
        failure_reason="provider failed"
        if status is TicketSubmissionStatus.FAILED
        else None,
        completed_by="ticket-worker-001",
        completed_at=NOW + timedelta(minutes=1),
        result_idempotency_key_hash=HASH,
        result_request_hash=HASH,
        result_trace_id="trc_result",
        **common,
    )


def test_valid_ticket_submission_requested_envelope_is_parsed() -> None:
    """有效消息应解析出提交请求、草稿和目标系统标识。"""
    event = TicketSubmissionRequestedEventV1.from_envelope(build_envelope())

    assert event.event_id == "evt_ticket_submission_requested_001"
    assert event.ticket_submission_id == "tsb_001"
    assert event.ticket_draft_id == "tdf_001"
    assert event.workflow_run_id == "wfr_001"
    assert event.target_system == "jira"
    assert event.draft_version == 2
    assert event.occurred_at == NOW


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("event_type", "ticket_submission.failed", "Unsupported event_type"),
        ("schema_version", 2, "schema_version"),
        ("aggregate_type", "WorkflowRun", "aggregate_type"),
        ("event_id", "", "event_id"),
        ("occurred_at", "2026-07-01T18:00:00", "timezone"),
    ],
)
def test_invalid_submission_envelope_metadata_is_rejected(
    field_name: str,
    value: object,
    message: str,
) -> None:
    """路由、版本、身份和时间元数据错误属于坏消息。"""
    envelope = build_envelope()
    envelope[field_name] = value

    with pytest.raises(MessageContractError, match=message):
        TicketSubmissionRequestedEventV1.from_envelope(envelope)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("event_id", "evt_ticket_submission_requested_001\nforged"),
        ("tenant_id", "tenant_001\rforged"),
        ("aggregate_id", "tsb_001\tforged"),
        ("trace_id", "trc_ticket_submission_001\x7fforged"),
        ("trace_id", "trc_ticket_submission_001\nforged"),
    ],
)
def test_submission_envelope_metadata_rejects_control_characters(
    field_name: str,
    value: str,
) -> None:
    """提交消息元数据会进入死信和审计链路，必须保持单行。"""
    envelope = build_envelope()
    envelope[field_name] = value

    with pytest.raises(MessageContractError, match="control characters"):
        TicketSubmissionRequestedEventV1.from_envelope(envelope)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("ticket_submission_id", "tsb_other", "ticket_submission_id do not match"),
        ("ticket_draft_id", "", "ticket_draft_id"),
        ("target_system", None, "target_system"),
        ("draft_version", True, "draft_version"),
        ("requested_at", "not-a-time", "ISO timestamp"),
    ],
)
def test_invalid_submission_payload_is_rejected(
    field_name: str,
    value: object,
    message: str,
) -> None:
    """Payload 字段错误不能进入外部工单调用流程。"""
    envelope = build_envelope()
    envelope["payload"][field_name] = value

    with pytest.raises(MessageContractError, match=message):
        TicketSubmissionRequestedEventV1.from_envelope(envelope)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("ticket_submission_id", "tsb_001\nforged"),
        ("ticket_draft_id", "tdf_001\rforged"),
        ("workflow_run_id", "wfr_001\tforged"),
        ("target_system", "jira\nforged"),
        ("requested_by", "submitter_001\x7fforged"),
        ("requested_by", "submitter_001\rforged"),
    ],
)
def test_submission_payload_rejects_control_characters(
    field_name: str,
    value: str,
) -> None:
    """工单提交路由字段不能携带换行或制表污染。"""
    envelope = build_envelope()
    envelope["payload"][field_name] = value

    with pytest.raises(MessageContractError, match="control characters"):
        TicketSubmissionRequestedEventV1.from_envelope(envelope)


class FakeTicketSubmissionRepository:
    """返回预设提交记录的仓储替身。"""

    def __init__(self, submission: TicketSubmission | None) -> None:
        self.submission = submission

    async def get_by_id(
        self,
        tenant_id: str,
        ticket_submission_id: str,
    ) -> TicketSubmission | None:
        assert tenant_id == "tenant_001"
        assert ticket_submission_id == "tsb_001"
        return self.submission


class FakeTicketDraftRepository:
    """返回预设草稿记录的仓储替身。"""

    def __init__(self, draft: TicketDraft | None) -> None:
        self.draft = draft

    async def get_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> TicketDraft | None:
        assert tenant_id == "tenant_001"
        assert workflow_run_id == "wfr_001"
        return self.draft


class FakeUnitOfWork:
    """只暴露 Handler 读取所需仓储的 UoW 替身。"""

    def __init__(
        self,
        submission: TicketSubmission | None,
        draft: TicketDraft | None,
    ) -> None:
        self.ticket_submissions = FakeTicketSubmissionRepository(submission)
        self.ticket_drafts = FakeTicketDraftRepository(draft)

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> bool:
        return False


class FakeGateway:
    """记录外部提交请求并返回预设结果或异常。"""

    def __init__(
        self,
        outcome: TicketingSubmitOutcome | None = None,
        error: Exception | None = None,
    ) -> None:
        self.outcome = outcome or TicketingSubmitOutcome(
            succeeded=True,
            external_ticket_id="JIRA-101",
            external_ticket_url="https://jira.example/browse/JIRA-101",
        )
        self.error = error
        self.requests: list[TicketingSubmitRequest] = []

    async def submit_ticket(
        self,
        request: TicketingSubmitRequest,
    ) -> TicketingSubmitOutcome:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.outcome


class FakeSubmissionService:
    """记录结果回填命令并返回对应视图。"""

    def __init__(self) -> None:
        self.commands: list[CompleteTicketSubmissionCommand] = []

    async def record_result(
        self,
        command: CompleteTicketSubmissionCommand,
    ) -> TicketSubmissionView:
        self.commands.append(command)
        return TicketSubmissionView(
            ticket_submission_id=command.ticket_submission_id,
            tenant_id=command.tenant_id,
            ticket_draft_id="tdf_001",
            workflow_run_id="wfr_001",
            target_system="jira",
            status=command.result_status.value,
            requested_by="submitter_001",
            trace_id=command.trace_id,
            requested_at=NOW,
            version=2,
            external_ticket_id=command.external_ticket_id,
            external_ticket_url=command.external_ticket_url,
            failure_reason=command.failure_reason,
            completed_by=command.completed_by,
            completed_at=NOW + timedelta(minutes=1),
            result_trace_id=command.trace_id,
        )


def build_handler(
    *,
    submission: TicketSubmission | None | object = _DEFAULT,
    draft: TicketDraft | None | object = _DEFAULT,
    gateway: FakeGateway | None = None,
    service: FakeSubmissionService | None = None,
) -> tuple[
    submission_handler.TicketSubmissionRequestedMessageHandler,
    FakeGateway,
    FakeSubmissionService,
]:
    """组装处理器和可观察替身。"""
    resolved_gateway = gateway or FakeGateway()
    resolved_service = service or FakeSubmissionService()
    resolved_submission = build_submission() if submission is _DEFAULT else submission
    resolved_draft = build_draft() if draft is _DEFAULT else draft
    handler = submission_handler.TicketSubmissionRequestedMessageHandler(
        unit_of_work_factory=lambda: FakeUnitOfWork(
            resolved_submission,
            resolved_draft,
        ),
        ticketing_gateway=resolved_gateway,
        submission_service=resolved_service,
        worker_id="ticket-worker-001",
    )
    return handler, resolved_gateway, resolved_service


async def test_handler_submits_ticket_and_records_success() -> None:
    """REQUESTED 消息应调用外部端口并记录 SUBMITTED 结果。"""
    handler, gateway, service = build_handler()
    event = TicketSubmissionRequestedEventV1.from_envelope(build_envelope())

    result = await handler.handle(event)

    assert (
        result.disposition
        is submission_handler.TicketSubmissionRequestedDisposition.SUBMITTED
    )
    assert result.submission_status == TicketSubmissionStatus.SUBMITTED.value
    assert result.external_ticket_id == "JIRA-101"
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.title == "Checkout outage"
    assert request.priority == "P1"
    assert request.idempotency_key == event.event_id
    command = service.commands[0]
    assert command.result_status is TicketSubmissionStatus.SUBMITTED
    assert command.expected_version == 1
    assert command.idempotency_key == f"{event.event_id}:result"
    assert command.completed_by == "ticket-worker-001"


async def test_handler_records_gateway_business_failure() -> None:
    """外部端口返回失败结果时应回填 FAILED，而不是抛异常重试。"""
    gateway = FakeGateway(
        TicketingSubmitOutcome(
            succeeded=False,
            failure_reason="provider validation failed",
        )
    )
    handler, _, service = build_handler(gateway=gateway)

    result = await handler.handle(
        TicketSubmissionRequestedEventV1.from_envelope(build_envelope())
    )

    assert (
        result.disposition
        is submission_handler.TicketSubmissionRequestedDisposition.FAILED
    )
    assert result.failure_reason == "provider validation failed"
    assert service.commands[0].result_status is TicketSubmissionStatus.FAILED
    assert service.commands[0].failure_reason == "provider validation failed"


async def test_handler_redacts_gateway_business_failure_reason() -> None:
    """自定义Gateway返回失败原因时，Handler仍要防止敏感文本落库。"""
    gateway = FakeGateway(
        TicketingSubmitOutcome(
            succeeded=False,
            failure_reason="provider password=hunter2 token=secret-token",
        )
    )
    handler, _, service = build_handler(gateway=gateway)

    result = await handler.handle(
        TicketSubmissionRequestedEventV1.from_envelope(build_envelope())
    )

    assert result.disposition is (
        submission_handler.TicketSubmissionRequestedDisposition.FAILED
    )
    assert result.failure_reason == ("provider password=[REDACTED] token=[REDACTED]")
    assert service.commands[0].failure_reason == result.failure_reason
    assert "hunter2" not in str(result.failure_reason)
    assert "secret-token" not in str(service.commands[0].failure_reason)


async def test_handler_ignores_terminal_duplicate_before_gateway_call() -> None:
    """终态提交的重复消息不能再次调用外部工单系统。"""
    handler, gateway, service = build_handler(
        submission=build_submission(status=TicketSubmissionStatus.SUBMITTED)
    )

    result = await handler.handle(
        TicketSubmissionRequestedEventV1.from_envelope(build_envelope())
    )

    assert (
        result.disposition
        is submission_handler.TicketSubmissionRequestedDisposition.IGNORED
    )
    assert result.submission_status == TicketSubmissionStatus.SUBMITTED.value
    assert gateway.requests == []
    assert service.commands == []


async def test_handler_rejects_message_that_does_not_match_database() -> None:
    """消息字段与本地事实不一致属于坏消息，不能调用外部系统。"""
    envelope = build_envelope()
    envelope["payload"]["target_system"] = "servicenow"
    handler, gateway, service = build_handler()

    with pytest.raises(MessageContractError, match="does not match"):
        await handler.handle(TicketSubmissionRequestedEventV1.from_envelope(envelope))

    assert gateway.requests == []
    assert service.commands == []


async def test_handler_requires_existing_submission_and_draft() -> None:
    """缺失本地提交记录或草稿时不能调用外部系统。"""
    missing_submission, gateway, _ = build_handler(submission=None)
    missing_draft, _, _ = build_handler(draft=None)
    event = TicketSubmissionRequestedEventV1.from_envelope(build_envelope())

    with pytest.raises(ResourceNotFound, match="Ticket submission"):
        await missing_submission.handle(event)
    with pytest.raises(ResourceNotFound, match="Ticket draft"):
        await missing_draft.handle(event)

    assert gateway.requests == []


async def test_handler_propagates_transient_gateway_error() -> None:
    """外部暂态异常应向上传播，交给未来记录处理器重试。"""
    gateway = FakeGateway(error=RuntimeError("gateway unavailable"))
    handler, _, service = build_handler(gateway=gateway)

    with pytest.raises(RuntimeError, match="gateway unavailable"):
        await handler.handle(
            TicketSubmissionRequestedEventV1.from_envelope(build_envelope())
        )

    assert len(gateway.requests) == 1
    assert service.commands == []


@pytest.mark.parametrize(
    "worker_id",
    [" ", "ticket\nforged", "w" * 129],
)
def test_handler_rejects_invalid_worker_id(worker_id: str) -> None:
    """错误 Worker 标识应在启动装配时暴露。"""
    with pytest.raises(AppValidationError, match="worker_id"):
        submission_handler.TicketSubmissionRequestedMessageHandler(
            unit_of_work_factory=lambda: FakeUnitOfWork(
                build_submission(),
                build_draft(),
            ),
            ticketing_gateway=FakeGateway(),
            submission_service=FakeSubmissionService(),
            worker_id=worker_id,
        )
