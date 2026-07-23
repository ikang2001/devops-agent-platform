import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from devops_agent_platform.application.commands.ticket_drafts import (
    CreateTicketDraftCommand,
    DecideTicketDraftCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.queries.ticket_drafts import (
    GetTicketDraftQuery,
)
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    TicketDraftStatus,
    TicketPriority,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.ticket_draft import (
    TicketDraft,
    build_ticket_request_hash,
)
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class TicketDraftView:
    """管理端可读取的不含幂等摘要的工单草稿视图。"""

    ticket_draft_id: str
    tenant_id: str
    incident_id: str
    workflow_run_id: str
    report_id: str
    status: str
    priority: str
    title: str
    description: str
    evidence_ids: tuple[str, ...]
    recommendations: tuple[str, ...]
    created_by: str
    trace_id: str
    created_at: datetime
    version: int
    decided_by: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    decision_trace_id: str | None = None
    is_duplicate: bool = False

    @classmethod
    def from_domain(
        cls,
        draft: TicketDraft,
        *,
        trace_id: str | None = None,
        is_duplicate: bool = False,
    ) -> "TicketDraftView":
        """从领域对象移除内部哈希并构造响应视图。"""
        return cls(
            ticket_draft_id=draft.ticket_draft_id,
            tenant_id=draft.tenant_id,
            incident_id=draft.incident_id,
            workflow_run_id=draft.workflow_run_id,
            report_id=draft.report_id,
            status=draft.status.value,
            priority=draft.priority.value,
            title=draft.title,
            description=draft.description,
            evidence_ids=draft.evidence_ids,
            recommendations=draft.recommendations,
            created_by=draft.created_by,
            trace_id=trace_id or draft.trace_id,
            created_at=draft.created_at,
            version=draft.version,
            decided_by=draft.decided_by,
            decision_reason=draft.decision_reason,
            decided_at=draft.decided_at,
            decision_trace_id=draft.decision_trace_id,
            is_duplicate=is_duplicate,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为统一响应 Envelope 中的稳定字典。"""
        return {
            "ticket_draft_id": self.ticket_draft_id,
            "tenant_id": self.tenant_id,
            "incident_id": self.incident_id,
            "workflow_run_id": self.workflow_run_id,
            "report_id": self.report_id,
            "status": self.status,
            "priority": self.priority,
            "title": self.title,
            "description": self.description,
            "evidence_ids": list(self.evidence_ids),
            "recommendations": list(self.recommendations),
            "created_by": self.created_by,
            "trace_id": self.trace_id,
            "created_at": self.created_at.isoformat(),
            "version": self.version,
            "decided_by": self.decided_by,
            "decision_reason": self.decision_reason,
            "decided_at": (
                self.decided_at.isoformat()
                if self.decided_at is not None
                else None
            ),
            "decision_trace_id": self.decision_trace_id,
            "is_duplicate": self.is_duplicate,
        }


class TicketDraftApplicationService:
    """从可信 RCA 事实原子生成并查询本地工单草稿。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create(
        self,
        command: CreateTicketDraftCommand,
    ) -> TicketDraftView:
        """创建草稿；并发同幂等请求在提交冲突后恢复原结果。"""
        idempotency_hash = hashlib.sha256(
            command.idempotency_key.encode("utf-8")
        ).hexdigest()
        request_hash = build_ticket_request_hash(
            command.tenant_id,
            command.workflow_run_id,
            command.requested_by,
        )
        try:
            return await self._create_once(
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

    async def get(
        self,
        query: GetTicketDraftQuery,
    ) -> TicketDraftView:
        """按租户边界读取工作流对应草稿。"""
        async with self._unit_of_work_factory() as unit_of_work:
            draft = await unit_of_work.ticket_drafts.get_by_workflow_run(
                query.tenant_id,
                query.workflow_run_id,
            )
        if draft is None:
            raise ResourceNotFound("Ticket draft not found")
        return TicketDraftView.from_domain(draft)

    async def decide(
        self,
        command: DecideTicketDraftCommand,
    ) -> TicketDraftView:
        """提交人工确认；并发同请求在冲突后恢复原终态。"""
        command = self._sanitize_decision_command(command)
        idempotency_hash = hashlib.sha256(
            command.idempotency_key.encode("utf-8")
        ).hexdigest()
        request_hash = self._decision_request_hash(command)
        try:
            return await self._decide_once(
                command,
                idempotency_hash,
                request_hash,
            )
        except ConflictError:
            recovered = await self._recover_decision(
                command,
                idempotency_hash,
                request_hash,
            )
            if recovered is not None:
                return recovered
            raise

    async def _create_once(
        self,
        command: CreateTicketDraftCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> TicketDraftView:
        """在单事务中读取来源、写草稿和追加审计事件。"""
        now = self._now()
        async with self._unit_of_work_factory() as unit_of_work:
            duplicate = (
                await unit_of_work.ticket_drafts
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
            existing = (
                await unit_of_work.ticket_drafts.get_by_workflow_run(
                    command.tenant_id,
                    command.workflow_run_id,
                )
            )
            if existing is not None:
                raise ConflictError(
                    "Ticket draft already exists for workflow run"
                )
            workflow = await unit_of_work.workflow_runs.get_by_id(
                command.tenant_id,
                command.workflow_run_id,
            )
            if workflow is None:
                raise ResourceNotFound("Workflow run not found")
            if workflow.status is not WorkflowRunStatus.SUCCEEDED:
                raise ConflictError(
                    "Ticket draft requires a succeeded workflow"
                )
            if workflow.audit_purged_at is not None:
                raise ConflictError(
                    "Ticket draft requires available audit evidence"
                )
            report = await unit_of_work.rca_reports.get_by_workflow_run(
                command.tenant_id,
                command.workflow_run_id,
            )
            if report is None:
                raise ConflictError(
                    "Ticket draft requires an RCA report"
                )
            if (
                report.incident_id != workflow.incident_id
                or report.workflow_run_id != workflow.workflow_run_id
            ):
                raise ConflictError(
                    "RCA report does not match workflow run"
                )
            incident = await unit_of_work.incidents.get_by_id(
                workflow.incident_id,
                command.tenant_id,
            )
            if incident is None:
                raise ResourceNotFound("Incident not found")
            draft = self._build_draft(
                command,
                report,
                incident.severity,
                idempotency_hash,
                request_hash,
                now,
            )
            await unit_of_work.ticket_drafts.save(draft)
            await unit_of_work.outbox.add(
                self._build_audit_event(draft)
            )
            await unit_of_work.commit()
        return TicketDraftView.from_domain(
            draft,
            trace_id=command.trace_id,
        )

    async def _recover_idempotent_result(
        self,
        command: CreateTicketDraftCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> TicketDraftView | None:
        """提交竞态后用新事务确认是否为同一请求。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = (
                await unit_of_work.ticket_drafts
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

    async def _decide_once(
        self,
        command: DecideTicketDraftCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> TicketDraftView:
        """在单事务中原子更新状态并追加不含拒绝正文的审计事件。"""
        now = self._now()
        async with self._unit_of_work_factory() as unit_of_work:
            duplicate = (
                await unit_of_work.ticket_drafts
                .get_by_decision_idempotency_key_hash(
                    command.tenant_id,
                    idempotency_hash,
                )
            )
            if duplicate is not None:
                return self._existing_decision(
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
            if draft.version != command.expected_version:
                raise ConflictError(
                    "Ticket draft decision version conflict"
                )
            decided = draft.decide(
                command.decision,
                decided_by=command.requested_by,
                reason=command.reason,
                decided_at=now,
                idempotency_key_hash=idempotency_hash,
                request_hash=request_hash,
                trace_id=command.trace_id,
            )
            await unit_of_work.ticket_drafts.apply_decision(
                decided,
                command.expected_version,
            )
            await unit_of_work.outbox.add(
                self._build_decision_event(decided)
            )
            await unit_of_work.commit()
        return TicketDraftView.from_domain(
            decided,
            trace_id=command.trace_id,
        )

    async def _recover_decision(
        self,
        command: DecideTicketDraftCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> TicketDraftView | None:
        """审批提交竞态后使用新事务确认同请求结果。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = (
                await unit_of_work.ticket_drafts
                .get_by_decision_idempotency_key_hash(
                    command.tenant_id,
                    idempotency_hash,
                )
            )
        if existing is None:
            return None
        return self._existing_decision(
            existing,
            command,
            request_hash,
        )

    @staticmethod
    def _existing_decision(
        draft: TicketDraft,
        command: DecideTicketDraftCommand,
        request_hash: str,
    ) -> TicketDraftView:
        """区分审批重放与幂等键误复用。"""
        if draft.decision_request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was used for another ticket decision"
            )
        return TicketDraftView.from_domain(
            draft,
            trace_id=command.trace_id,
            is_duplicate=True,
        )

    @staticmethod
    def _existing_result(
        draft: TicketDraft,
        command: CreateTicketDraftCommand,
        request_hash: str,
    ) -> TicketDraftView:
        """区分同请求重放与幂等键复用于其它创建意图。"""
        if draft.request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was used for another ticket request"
            )
        return TicketDraftView.from_domain(
            draft,
            trace_id=command.trace_id,
            is_duplicate=True,
        )

    def _build_draft(
        self,
        command: CreateTicketDraftCommand,
        report: RCAReport,
        severity: AlertSeverity,
        idempotency_hash: str,
        request_hash: str,
        now: datetime,
    ) -> TicketDraft:
        """从报告和事故严重度生成不可由 HTTP 篡改的草稿内容。"""
        title = self._safe_report_text(
            report.title,
            multiline=False,
            maximum=256,
        )
        summary = self._safe_report_text(
            report.summary,
            multiline=True,
            maximum=4096,
        )
        recommendations = self._safe_recommendations(
            report.recommendations
        )
        description = (
            f"RCA conclusion: {report.conclusion_status.value}\n"
            f"Confidence: {float(report.confidence):.4f}\n\n"
            f"{summary}"
        )
        return TicketDraft(
            ticket_draft_id=(
                self._identifier_generator.new_ticket_draft_id()
            ),
            tenant_id=command.tenant_id,
            incident_id=report.incident_id,
            workflow_run_id=command.workflow_run_id,
            report_id=report.report_id,
            status=TicketDraftStatus.DRAFT,
            priority=self._priority(severity),
            title=title,
            description=description,
            evidence_ids=report.evidence_ids,
            recommendations=recommendations,
            created_by=command.requested_by,
            idempotency_key_hash=idempotency_hash,
            request_hash=request_hash,
            trace_id=command.trace_id,
            created_at=now,
        )

    def _build_audit_event(self, draft: TicketDraft) -> OutboxEvent:
        """构造不包含描述和建议正文的工单草稿审计事件。"""
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=draft.tenant_id,
            aggregate_type="TicketDraft",
            aggregate_id=draft.ticket_draft_id,
            event_type="ticket_draft.created",
            schema_version=1,
            payload={
                "ticket_draft_id": draft.ticket_draft_id,
                "incident_id": draft.incident_id,
                "workflow_run_id": draft.workflow_run_id,
                "report_id": draft.report_id,
                "priority": draft.priority.value,
                "evidence_count": len(draft.evidence_ids),
                "recommendation_count": len(
                    draft.recommendations
                ),
                "created_by": draft.created_by,
                "created_at": draft.created_at.isoformat(),
            },
            occurred_at=draft.created_at,
            trace_id=draft.trace_id,
        )

    def _build_decision_event(self, draft: TicketDraft) -> OutboxEvent:
        """构造人工确认审计事件，拒绝原因只发送摘要。"""
        reason_hash = (
            hashlib.sha256(
                draft.decision_reason.encode("utf-8")
            ).hexdigest()
            if draft.decision_reason is not None
            else None
        )
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=draft.tenant_id,
            aggregate_type="TicketDraft",
            aggregate_id=draft.ticket_draft_id,
            event_type=(
                "ticket_draft.approved"
                if draft.status is TicketDraftStatus.APPROVED
                else "ticket_draft.rejected"
            ),
            schema_version=1,
            payload={
                "ticket_draft_id": draft.ticket_draft_id,
                "workflow_run_id": draft.workflow_run_id,
                "decision": draft.status.value,
                "decided_by": draft.decided_by,
                "decision_reason_sha256": reason_hash,
                "decided_at": (
                    draft.decided_at.isoformat()
                    if draft.decided_at is not None
                    else None
                ),
                "version": draft.version,
            },
            occurred_at=draft.decided_at or draft.created_at,
            trace_id=draft.decision_trace_id or draft.trace_id,
        )

    @staticmethod
    def _decision_request_hash(
        command: DecideTicketDraftCommand,
    ) -> str:
        """生成字段顺序稳定的人工确认请求指纹。"""
        encoded = json.dumps(
            {
                "tenant_id": command.tenant_id,
                "workflow_run_id": command.workflow_run_id,
                "decision": command.decision.value,
                "reason": command.reason,
                "expected_version": command.expected_version,
                "requested_by": command.requested_by,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _sanitize_decision_command(
        command: DecideTicketDraftCommand,
    ) -> DecideTicketDraftCommand:
        """在人工拒绝原因进入业务表和幂等指纹前兜底脱敏。"""
        if command.reason is None:
            return command
        reason = " ".join(command.reason.splitlines()).strip()
        safe_reason = redact_sensitive_text(reason)[0][:2048].strip()
        if safe_reason == command.reason:
            return command
        return replace(command, reason=safe_reason)

    @staticmethod
    def _priority(severity: AlertSeverity) -> TicketPriority:
        """使用固定规则映射事故严重度，避免调用方自行提权。"""
        return {
            AlertSeverity.CRITICAL: TicketPriority.P1,
            AlertSeverity.WARNING: TicketPriority.P2,
            AlertSeverity.INFO: TicketPriority.P3,
        }[severity]

    @staticmethod
    def _safe_text(value: str, *, multiline: bool) -> str:
        """转义不允许的控制字符，保留可审核正文。"""
        result: list[str] = []
        for character in value:
            if character == "\n" and multiline:
                result.append(character)
            elif ord(character) < 32 or ord(character) == 127:
                result.append(f"\\u{ord(character):04x}")
            else:
                result.append(character)
        return "".join(result).strip()

    @classmethod
    def _safe_report_text(
        cls,
        value: str,
        *,
        multiline: bool,
        maximum: int,
    ) -> str:
        """报告文本进入工单草稿前再次转义、脱敏并限制容量。"""
        text = cls._safe_text(value, multiline=multiline)
        text = redact_sensitive_text(text)[0][:maximum].strip()
        if not text:
            raise AppValidationError("report text is empty after sanitization")
        return text

    @classmethod
    def _safe_recommendations(
        cls,
        recommendations: tuple[str, ...],
    ) -> tuple[str, ...]:
        """建议项进入工单草稿前脱敏，并按脱敏后文本保持唯一。"""
        result: list[str] = []
        seen: set[str] = set()
        for item in recommendations:
            safe_item = cls._safe_report_text(
                item,
                multiline=False,
                maximum=1024,
            )
            if safe_item in seen:
                continue
            seen.add(safe_item)
            result.append(safe_item)
        return tuple(result)

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
