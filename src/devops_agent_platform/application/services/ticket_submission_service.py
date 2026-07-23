import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from devops_agent_platform.application.commands.ticket_drafts import (
    CompleteTicketSubmissionCommand,
    SubmitTicketDraftCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.queries.ticket_drafts import (
    ListTicketSubmissionsQuery,
)
from devops_agent_platform.domain.enums import (
    TicketDraftStatus,
    TicketSubmissionStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.ticket_draft import TicketDraft
from devops_agent_platform.domain.models.ticket_submission import TicketSubmission
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class TicketSubmissionView:
    """管理端可读取的不含幂等摘要的提交请求视图。"""

    ticket_submission_id: str
    tenant_id: str
    ticket_draft_id: str
    workflow_run_id: str
    target_system: str
    status: str
    requested_by: str
    trace_id: str
    requested_at: datetime
    version: int
    external_ticket_id: str | None = None
    external_ticket_url: str | None = None
    failure_reason: str | None = None
    completed_by: str | None = None
    completed_at: datetime | None = None
    result_trace_id: str | None = None
    is_duplicate: bool = False

    @classmethod
    def from_domain(
        cls,
        submission: TicketSubmission,
        *,
        trace_id: str | None = None,
        is_duplicate: bool = False,
    ) -> "TicketSubmissionView":
        """从领域对象移除内部哈希并构造响应视图。"""
        return cls(
            ticket_submission_id=submission.ticket_submission_id,
            tenant_id=submission.tenant_id,
            ticket_draft_id=submission.ticket_draft_id,
            workflow_run_id=submission.workflow_run_id,
            target_system=submission.target_system,
            status=submission.status.value,
            requested_by=submission.requested_by,
            trace_id=trace_id or submission.trace_id,
            requested_at=submission.requested_at,
            version=submission.version,
            external_ticket_id=submission.external_ticket_id,
            external_ticket_url=submission.external_ticket_url,
            failure_reason=submission.failure_reason,
            completed_by=submission.completed_by,
            completed_at=submission.completed_at,
            result_trace_id=submission.result_trace_id,
            is_duplicate=is_duplicate,
        )

    def to_dict(self, *, include_failure_reason: bool = False) -> dict[str, Any]:
        """转换为统一响应 Envelope 中的稳定字典。

        默认不返回外部系统失败原文。原文保留在业务表和应用视图中，HTTP 响应只
        暴露稳定摘要，避免 provider 错误正文携带内部地址、请求标识或凭据片段。
        """
        failure_reason_sha256 = (
            hashlib.sha256(self.failure_reason.encode("utf-8")).hexdigest()
            if self.failure_reason is not None
            else None
        )
        return {
            "ticket_submission_id": self.ticket_submission_id,
            "tenant_id": self.tenant_id,
            "ticket_draft_id": self.ticket_draft_id,
            "workflow_run_id": self.workflow_run_id,
            "target_system": self.target_system,
            "status": self.status,
            "requested_by": self.requested_by,
            "trace_id": self.trace_id,
            "requested_at": self.requested_at.isoformat(),
            "version": self.version,
            "external_ticket_id": self.external_ticket_id,
            "external_ticket_url": self.external_ticket_url,
            "failure_reason": (
                self.failure_reason if include_failure_reason else None
            ),
            "failure_reason_sha256": failure_reason_sha256,
            "completed_by": self.completed_by,
            "completed_at": (
                self.completed_at.isoformat()
                if self.completed_at is not None
                else None
            ),
            "result_trace_id": self.result_trace_id,
            "is_duplicate": self.is_duplicate,
        }


class TicketSubmissionApplicationService:
    """把已审批 Ticket Draft 原子登记为外部提交请求。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def request_submission(
        self,
        command: SubmitTicketDraftCommand,
    ) -> TicketSubmissionView:
        """登记外部提交请求；同请求重放返回原结果。"""
        idempotency_hash = self._hash_text(command.idempotency_key)
        request_hash = self._request_hash(command)
        try:
            return await self._request_once(
                command,
                idempotency_hash,
                request_hash,
            )
        except ConflictError:
            recovered = await self._recover_idempotent_result(
                command,
                idempotency_hash,
                request_hash,
            )
            if recovered is not None:
                return recovered
            raise

    async def record_result(
        self,
        command: CompleteTicketSubmissionCommand,
    ) -> TicketSubmissionView:
        """记录外部工单提交结果；同结果重放返回原终态。"""
        command = self._sanitize_result_command(command)
        idempotency_hash = self._hash_text(command.idempotency_key)
        request_hash = self._result_request_hash(command)
        try:
            return await self._record_result_once(
                command,
                idempotency_hash,
                request_hash,
            )
        except ConflictError:
            recovered = await self._recover_result(
                command,
                idempotency_hash,
                request_hash,
            )
            if recovered is not None:
                return recovered
            raise

    async def list_by_workflow(
        self,
        query: ListTicketSubmissionsQuery,
    ) -> tuple[TicketSubmissionView, ...]:
        """读取一次工作流下的外部提交状态，不产生事务副作用。"""
        async with self._unit_of_work_factory() as unit_of_work:
            submissions = await unit_of_work.ticket_submissions.list_by_workflow(
                query.tenant_id,
                query.workflow_run_id,
                limit=query.limit,
            )
        return tuple(
            TicketSubmissionView.from_domain(submission)
            for submission in submissions
        )

    async def _request_once(
        self,
        command: SubmitTicketDraftCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> TicketSubmissionView:
        """在单事务中校验草稿、保存提交请求并写入 Outbox。"""
        now = self._now()
        async with self._unit_of_work_factory() as unit_of_work:
            duplicate = (
                await unit_of_work.ticket_submissions
                .get_by_idempotency_key_hash(
                    command.tenant_id,
                    idempotency_hash,
                )
            )
            if duplicate is not None:
                return self._existing_result(
                    duplicate,
                    command,
                    request_hash,
                )
            draft = await unit_of_work.ticket_drafts.get_by_workflow_run(
                command.tenant_id,
                command.workflow_run_id,
            )
            if draft is None:
                raise ResourceNotFound("Ticket draft not found")
            self._ensure_submit_allowed(draft, command)
            existing_for_target = (
                await unit_of_work.ticket_submissions.get_by_draft_and_target(
                    command.tenant_id,
                    draft.ticket_draft_id,
                    command.target_system,
                )
            )
            if existing_for_target is not None:
                raise ConflictError(
                    "Ticket draft was already submitted to target system"
                )
            submission = self._build_submission(
                command,
                draft,
                idempotency_hash,
                request_hash,
                now,
            )
            await unit_of_work.ticket_submissions.save(submission)
            await unit_of_work.outbox.add(
                self._build_submission_event(submission, draft)
            )
            await unit_of_work.commit()
        return TicketSubmissionView.from_domain(
            submission,
            trace_id=command.trace_id,
        )

    async def _recover_idempotent_result(
        self,
        command: SubmitTicketDraftCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> TicketSubmissionView | None:
        """提交竞态后用新事务确认是否为同一提交请求。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = (
                await unit_of_work.ticket_submissions
                .get_by_idempotency_key_hash(
                    command.tenant_id,
                    idempotency_hash,
                )
            )
        if existing is None:
            return None
        return self._existing_result(
            existing,
            command,
            request_hash,
        )

    async def _record_result_once(
        self,
        command: CompleteTicketSubmissionCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> TicketSubmissionView:
        """在单事务中原子写入外部提交终态和审计事件。"""
        now = self._now()
        async with self._unit_of_work_factory() as unit_of_work:
            duplicate = (
                await unit_of_work.ticket_submissions
                .get_by_result_idempotency_key_hash(
                    command.tenant_id,
                    idempotency_hash,
                )
            )
            if duplicate is not None:
                return self._existing_result_record(
                    duplicate,
                    command,
                    request_hash,
                )
            submission = await unit_of_work.ticket_submissions.get_by_id(
                command.tenant_id,
                command.ticket_submission_id,
            )
            if submission is None:
                raise ResourceNotFound("Ticket submission not found")
            if submission.status is not TicketSubmissionStatus.REQUESTED:
                raise ConflictError("Ticket submission result is already final")
            if submission.version != command.expected_version:
                raise ConflictError(
                    "Ticket submission result version conflict"
                )
            completed = self._complete_submission(
                submission,
                command,
                idempotency_hash,
                request_hash,
                now,
            )
            await unit_of_work.ticket_submissions.apply_result(
                completed,
                command.expected_version,
            )
            await unit_of_work.outbox.add(
                self._build_result_event(completed)
            )
            await unit_of_work.commit()
        return TicketSubmissionView.from_domain(
            completed,
            trace_id=command.trace_id,
        )

    async def _recover_result(
        self,
        command: CompleteTicketSubmissionCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> TicketSubmissionView | None:
        """结果回填竞态后使用新事务确认同请求结果。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = (
                await unit_of_work.ticket_submissions
                .get_by_result_idempotency_key_hash(
                    command.tenant_id,
                    idempotency_hash,
                )
            )
        if existing is None:
            return None
        return self._existing_result_record(
            existing,
            command,
            request_hash,
        )

    @staticmethod
    def _existing_result(
        submission: TicketSubmission,
        command: SubmitTicketDraftCommand,
        request_hash: str,
    ) -> TicketSubmissionView:
        """区分提交重放与幂等键误复用。"""
        if submission.request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was used for another ticket submission"
            )
        return TicketSubmissionView.from_domain(
            submission,
            trace_id=command.trace_id,
            is_duplicate=True,
        )

    @staticmethod
    def _existing_result_record(
        submission: TicketSubmission,
        command: CompleteTicketSubmissionCommand,
        request_hash: str,
    ) -> TicketSubmissionView:
        """区分结果回填重放与幂等键误复用。"""
        if submission.result_request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was used for another ticket submission result"
            )
        return TicketSubmissionView.from_domain(
            submission,
            trace_id=command.trace_id,
            is_duplicate=True,
        )

    @staticmethod
    def _ensure_submit_allowed(
        draft: TicketDraft,
        command: SubmitTicketDraftCommand,
    ) -> None:
        """校验只有已批准且版本匹配的草稿允许提交。"""
        if draft.status is not TicketDraftStatus.APPROVED:
            raise ConflictError(
                "Ticket submission requires an approved draft"
            )
        if draft.version != command.expected_draft_version:
            raise ConflictError(
                "Ticket submission draft version conflict"
            )

    def _build_submission(
        self,
        command: SubmitTicketDraftCommand,
        draft: TicketDraft,
        idempotency_hash: str,
        request_hash: str,
        now: datetime,
    ) -> TicketSubmission:
        """创建只包含提交请求事实的不可变领域对象。"""
        return TicketSubmission(
            ticket_submission_id=(
                self._identifier_generator.new_ticket_submission_id()
            ),
            tenant_id=command.tenant_id,
            ticket_draft_id=draft.ticket_draft_id,
            workflow_run_id=command.workflow_run_id,
            target_system=command.target_system,
            status=TicketSubmissionStatus.REQUESTED,
            idempotency_key_hash=idempotency_hash,
            request_hash=request_hash,
            requested_by=command.requested_by,
            trace_id=command.trace_id,
            requested_at=now,
        )

    @staticmethod
    def _complete_submission(
        submission: TicketSubmission,
        command: CompleteTicketSubmissionCommand,
        idempotency_hash: str,
        request_hash: str,
        now: datetime,
    ) -> TicketSubmission:
        """按命令终态选择成功或失败转换。"""
        if command.result_status is TicketSubmissionStatus.SUBMITTED:
            return submission.mark_submitted(
                external_ticket_id=command.external_ticket_id or "",
                external_ticket_url=command.external_ticket_url,
                completed_at=now,
                idempotency_key_hash=idempotency_hash,
                request_hash=request_hash,
                trace_id=command.trace_id,
                completed_by=command.completed_by,
            )
        return submission.mark_failed(
            failure_reason=command.failure_reason or "",
            completed_at=now,
            idempotency_key_hash=idempotency_hash,
            request_hash=request_hash,
            trace_id=command.trace_id,
            completed_by=command.completed_by,
        )

    @staticmethod
    def _sanitize_result_command(
        command: CompleteTicketSubmissionCommand,
    ) -> CompleteTicketSubmissionCommand:
        """在最终写入业务表前兜底清洗外部失败原因。"""
        if (
            command.result_status is not TicketSubmissionStatus.FAILED
            or command.failure_reason is None
        ):
            return command
        reason = " ".join(command.failure_reason.splitlines()).strip()
        safe_reason = redact_sensitive_text(reason)[0][:2048].strip()
        if safe_reason == command.failure_reason:
            return command
        return replace(command, failure_reason=safe_reason)

    def _build_submission_event(
        self,
        submission: TicketSubmission,
        draft: TicketDraft,
    ) -> OutboxEvent:
        """构造外部提交请求事件，不携带工单描述和建议正文。"""
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=submission.tenant_id,
            aggregate_type="TicketSubmission",
            aggregate_id=submission.ticket_submission_id,
            event_type="ticket_submission.requested",
            schema_version=1,
            payload={
                "ticket_submission_id": submission.ticket_submission_id,
                "ticket_draft_id": submission.ticket_draft_id,
                "workflow_run_id": submission.workflow_run_id,
                "target_system": submission.target_system,
                "draft_version": draft.version,
                "requested_by": submission.requested_by,
                "requested_at": submission.requested_at.isoformat(),
            },
            occurred_at=submission.requested_at,
            trace_id=submission.trace_id,
        )

    def _build_result_event(
        self,
        submission: TicketSubmission,
    ) -> OutboxEvent:
        """构造外部提交结果审计事件，失败原因只发送摘要。"""
        failure_hash = (
            hashlib.sha256(
                submission.failure_reason.encode("utf-8")
            ).hexdigest()
            if submission.failure_reason is not None
            else None
        )
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=submission.tenant_id,
            aggregate_type="TicketSubmission",
            aggregate_id=submission.ticket_submission_id,
            event_type=(
                "ticket_submission.submitted"
                if submission.status is TicketSubmissionStatus.SUBMITTED
                else "ticket_submission.failed"
            ),
            schema_version=1,
            payload={
                "ticket_submission_id": submission.ticket_submission_id,
                "ticket_draft_id": submission.ticket_draft_id,
                "workflow_run_id": submission.workflow_run_id,
                "target_system": submission.target_system,
                "status": submission.status.value,
                "external_ticket_id": submission.external_ticket_id,
                "failure_reason_sha256": failure_hash,
                "completed_by": submission.completed_by,
                "completed_at": (
                    submission.completed_at.isoformat()
                    if submission.completed_at is not None
                    else None
                ),
                "version": submission.version,
            },
            occurred_at=submission.completed_at or submission.requested_at,
            trace_id=submission.result_trace_id or submission.trace_id,
        )

    @staticmethod
    def _request_hash(command: SubmitTicketDraftCommand) -> str:
        """生成字段顺序稳定的外部提交请求指纹。"""
        encoded = json.dumps(
            {
                "tenant_id": command.tenant_id,
                "workflow_run_id": command.workflow_run_id,
                "target_system": command.target_system,
                "expected_draft_version": command.expected_draft_version,
                "requested_by": command.requested_by,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _result_request_hash(
        command: CompleteTicketSubmissionCommand,
    ) -> str:
        """生成字段顺序稳定的提交结果回填指纹。"""
        encoded = json.dumps(
            {
                "tenant_id": command.tenant_id,
                "ticket_submission_id": command.ticket_submission_id,
                "result_status": command.result_status.value,
                "external_ticket_id": command.external_ticket_id,
                "external_ticket_url": command.external_ticket_url,
                "failure_reason": command.failure_reason,
                "expected_version": command.expected_version,
                "completed_by": command.completed_by,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _hash_text(value: str) -> str:
        """只持久化幂等键摘要，降低数据库泄漏影响。"""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _now(self) -> datetime:
        """读取并规范化带时区应用时钟。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(
                "clock must return a timezone-aware datetime"
            )
        return value.astimezone(UTC)
