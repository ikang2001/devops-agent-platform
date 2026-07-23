from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from devops_agent_platform.application.commands.ticket_drafts import (
    CompleteTicketSubmissionCommand,
)
from devops_agent_platform.application.exceptions import MessageContractError
from devops_agent_platform.application.messages.ticket_submission_requested import (
    TicketSubmissionRequestedEventV1,
)
from devops_agent_platform.application.services.ticket_submission_service import (
    TicketSubmissionApplicationService,
    TicketSubmissionView,
)
from devops_agent_platform.domain.enums import (
    TicketDraftStatus,
    TicketSubmissionStatus,
)
from devops_agent_platform.domain.exceptions import ResourceNotFound
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.domain.models.ticket_draft import TicketDraft
from devops_agent_platform.domain.models.ticket_submission import TicketSubmission
from devops_agent_platform.ports.ticketing import (
    TicketingGatewayPort,
    TicketingSubmitOutcome,
    TicketingSubmitRequest,
)
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]


class TicketSubmissionRequestedDisposition(StrEnum):
    """提交请求事件处理器的稳定处置结果。"""

    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"
    IGNORED = "IGNORED"


@dataclass(frozen=True)
class TicketSubmissionRequestedHandlingResult:
    """提交请求消息处理完成后的应用层结果。"""

    event_id: str
    ticket_submission_id: str
    disposition: TicketSubmissionRequestedDisposition
    submission_status: str
    trace_id: str
    external_ticket_id: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class _SubmissionSource:
    """处理器内部使用的提交请求与草稿一致性快照。"""

    submission: TicketSubmission
    draft: TicketDraft


class TicketSubmissionRequestedMessageHandler:
    """处理 ticket_submission.requested 消息并记录外部提交结果。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        ticketing_gateway: TicketingGatewayPort,
        submission_service: TicketSubmissionApplicationService,
        worker_id: str,
    ) -> None:
        validate_worker_id(worker_id)
        self._unit_of_work_factory = unit_of_work_factory
        self._ticketing_gateway = ticketing_gateway
        self._submission_service = submission_service
        self._worker_id = worker_id

    async def handle(
        self,
        event: TicketSubmissionRequestedEventV1,
    ) -> TicketSubmissionRequestedHandlingResult:
        """加载提交上下文、调用外部端口并回填终态。"""
        source = await self._load_source(event)
        if source.submission.status is not TicketSubmissionStatus.REQUESTED:
            return TicketSubmissionRequestedHandlingResult(
                event_id=event.event_id,
                ticket_submission_id=event.ticket_submission_id,
                disposition=TicketSubmissionRequestedDisposition.IGNORED,
                submission_status=source.submission.status.value,
                trace_id=event.trace_id,
                external_ticket_id=source.submission.external_ticket_id,
                failure_reason=source.submission.failure_reason,
            )

        outcome = await self._ticketing_gateway.submit_ticket(
            self._build_gateway_request(event, source)
        )
        result = await self._record_outcome(event, source, outcome)
        disposition = (
            TicketSubmissionRequestedDisposition.SUBMITTED
            if outcome.succeeded
            else TicketSubmissionRequestedDisposition.FAILED
        )
        return TicketSubmissionRequestedHandlingResult(
            event_id=event.event_id,
            ticket_submission_id=event.ticket_submission_id,
            disposition=disposition,
            submission_status=result.status,
            trace_id=result.trace_id,
            external_ticket_id=result.external_ticket_id,
            failure_reason=result.failure_reason,
        )

    async def _load_source(
        self,
        event: TicketSubmissionRequestedEventV1,
    ) -> _SubmissionSource:
        """读取提交请求和草稿，并检查消息与数据库事实一致。"""
        async with self._unit_of_work_factory() as unit_of_work:
            submission = await unit_of_work.ticket_submissions.get_by_id(
                event.tenant_id,
                event.ticket_submission_id,
            )
            if submission is None:
                raise ResourceNotFound("Ticket submission not found")
            draft = await unit_of_work.ticket_drafts.get_by_workflow_run(
                event.tenant_id,
                event.workflow_run_id,
            )
        if draft is None:
            raise ResourceNotFound("Ticket draft not found")
        self._validate_source(event, submission, draft)
        return _SubmissionSource(submission=submission, draft=draft)

    @staticmethod
    def _validate_source(
        event: TicketSubmissionRequestedEventV1,
        submission: TicketSubmission,
        draft: TicketDraft,
    ) -> None:
        """拒绝消息字段与本地事实不一致的坏消息。"""
        if (
            submission.ticket_draft_id != event.ticket_draft_id
            or submission.workflow_run_id != event.workflow_run_id
            or submission.target_system != event.target_system
            or draft.ticket_draft_id != event.ticket_draft_id
            or draft.version != event.draft_version
        ):
            raise MessageContractError(
                "Ticket submission message does not match stored data"
            )
        if draft.status is not TicketDraftStatus.APPROVED:
            raise MessageContractError(
                "Ticket submission requires an approved draft"
            )

    @staticmethod
    def _build_gateway_request(
        event: TicketSubmissionRequestedEventV1,
        source: _SubmissionSource,
    ) -> TicketingSubmitRequest:
        """把本地草稿转换成外部工单端口载荷。"""
        return TicketingSubmitRequest(
            tenant_id=event.tenant_id,
            ticket_submission_id=source.submission.ticket_submission_id,
            ticket_draft_id=source.draft.ticket_draft_id,
            target_system=source.submission.target_system,
            title=source.draft.title,
            description=source.draft.description,
            priority=source.draft.priority.value,
            evidence_ids=source.draft.evidence_ids,
            recommendations=source.draft.recommendations,
            idempotency_key=event.event_id,
            trace_id=event.trace_id,
        )

    async def _record_outcome(
        self,
        event: TicketSubmissionRequestedEventV1,
        source: _SubmissionSource,
        outcome: TicketingSubmitOutcome,
    ) -> TicketSubmissionView:
        """把外部端口结果转换为提交结果回填命令。"""
        status = (
            TicketSubmissionStatus.SUBMITTED
            if outcome.succeeded
            else TicketSubmissionStatus.FAILED
        )
        return await self._submission_service.record_result(
            CompleteTicketSubmissionCommand(
                tenant_id=event.tenant_id,
                ticket_submission_id=source.submission.ticket_submission_id,
                result_status=status,
                expected_version=source.submission.version,
                idempotency_key=f"{event.event_id}:result",
                completed_by=self._worker_id,
                trace_id=event.trace_id,
                external_ticket_id=outcome.external_ticket_id,
                external_ticket_url=outcome.external_ticket_url,
                failure_reason=self._safe_failure_reason(
                    outcome.failure_reason
                ),
            )
        )

    @staticmethod
    def _safe_failure_reason(value: str | None) -> str | None:
        """清洗自定义工单网关返回的失败原因，避免敏感文本落库。"""
        if value is None:
            return None
        reason = " ".join(str(value).splitlines()).strip()
        if not reason:
            return reason
        return redact_sensitive_text(reason)[0][:2048]
