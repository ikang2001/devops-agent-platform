import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

from devops_agent_platform.application.commands.rca_feedback import (
    CreateRCAFeedbackCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.queries.rca_feedback import (
    ListRCAFeedbackQuery,
)
from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.rca_feedback import RCAFeedback
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class RCAFeedbackView:
    """管理端可读取且不包含幂等摘要的反馈视图。"""

    feedback_id: str
    tenant_id: str
    workflow_run_id: str
    report_id: str
    verdict: str
    corrected_root_cause: str | None
    missing_evidence_types: tuple[str, ...]
    unsafe_recommendation_indexes: tuple[int, ...]
    follow_up_label: str | None
    notes: str | None
    created_by: str
    trace_id: str
    created_at: datetime
    is_duplicate: bool = False

    @classmethod
    def from_domain(
        cls,
        feedback: RCAFeedback,
        *,
        is_duplicate: bool = False,
    ) -> "RCAFeedbackView":
        return cls(
            feedback_id=feedback.feedback_id,
            tenant_id=feedback.tenant_id,
            workflow_run_id=feedback.workflow_run_id,
            report_id=feedback.report_id,
            verdict=feedback.verdict.value,
            corrected_root_cause=feedback.corrected_root_cause,
            missing_evidence_types=tuple(
                item.value for item in feedback.missing_evidence_types
            ),
            unsafe_recommendation_indexes=(feedback.unsafe_recommendation_indexes),
            follow_up_label=feedback.follow_up_label,
            notes=feedback.notes,
            created_by=feedback.created_by,
            trace_id=feedback.trace_id,
            created_at=feedback.created_at,
            is_duplicate=is_duplicate,
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["missing_evidence_types"] = list(self.missing_evidence_types)
        result["unsafe_recommendation_indexes"] = list(
            self.unsafe_recommendation_indexes
        )
        result["created_at"] = self.created_at.isoformat()
        return result


class RCAFeedbackApplicationService:
    """持久化人工复核，并将其作为后续评测数据的可信输入。"""

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
        command: CreateRCAFeedbackCommand,
    ) -> RCAFeedbackView:
        sanitized = self._sanitize_command(command)
        idempotency_hash = hashlib.sha256(
            sanitized.idempotency_key.encode("utf-8")
        ).hexdigest()
        request_hash = self._request_hash(sanitized)
        try:
            return await self._create_once(
                sanitized,
                idempotency_hash,
                request_hash,
            )
        except ConflictError:
            recovered = await self._recover(
                sanitized.tenant_id,
                idempotency_hash,
                request_hash,
            )
            if recovered is not None:
                return recovered
            raise

    async def list(
        self,
        query: ListRCAFeedbackQuery,
    ) -> tuple[RCAFeedbackView, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            workflow = await unit_of_work.workflow_runs.get_by_id(
                query.tenant_id,
                query.workflow_run_id,
            )
            if workflow is None:
                raise ResourceNotFound("Workflow run not found")
            feedback = await unit_of_work.rca_feedback.list_by_workflow_run(
                query.tenant_id,
                query.workflow_run_id,
                query.limit,
            )
        return tuple(RCAFeedbackView.from_domain(item) for item in feedback)

    async def _create_once(
        self,
        command: CreateRCAFeedbackCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> RCAFeedbackView:
        async with self._unit_of_work_factory() as unit_of_work:
            duplicate = await unit_of_work.rca_feedback.get_by_idempotency_key_hash(
                command.tenant_id,
                idempotency_hash,
            )
            if duplicate is not None:
                return self._existing(duplicate, request_hash)
            workflow = await unit_of_work.workflow_runs.get_by_id(
                command.tenant_id,
                command.workflow_run_id,
            )
            if workflow is None:
                raise ResourceNotFound("Workflow run not found")
            if workflow.status is not WorkflowRunStatus.SUCCEEDED:
                raise ConflictError("RCA feedback requires a succeeded workflow")
            if workflow.audit_purged_at is not None:
                raise ConflictError("RCA feedback requires available audit evidence")
            report = await unit_of_work.rca_reports.get_by_workflow_run(
                command.tenant_id,
                command.workflow_run_id,
            )
            if report is None:
                raise ConflictError("RCA feedback requires a report")
            if any(
                index >= len(report.recommendations)
                for index in command.unsafe_recommendation_indexes
            ):
                raise AppValidationError("unsafe recommendation index is out of range")
            feedback = RCAFeedback(
                feedback_id=(self._identifier_generator.new_rca_feedback_id()),
                tenant_id=command.tenant_id,
                workflow_run_id=command.workflow_run_id,
                report_id=report.report_id,
                verdict=command.verdict,
                corrected_root_cause=command.corrected_root_cause,
                missing_evidence_types=command.missing_evidence_types,
                unsafe_recommendation_indexes=(command.unsafe_recommendation_indexes),
                follow_up_label=command.follow_up_label,
                notes=command.notes,
                created_by=command.requested_by,
                idempotency_key_hash=idempotency_hash,
                request_hash=request_hash,
                trace_id=command.trace_id,
                created_at=self._now(),
            )
            await unit_of_work.rca_feedback.save(feedback)
            await unit_of_work.outbox.add(self._build_audit_event(feedback))
            await unit_of_work.commit()
        return RCAFeedbackView.from_domain(feedback)

    async def _recover(
        self,
        tenant_id: str,
        idempotency_hash: str,
        request_hash: str,
    ) -> RCAFeedbackView | None:
        async with self._unit_of_work_factory() as unit_of_work:
            existing = await unit_of_work.rca_feedback.get_by_idempotency_key_hash(
                tenant_id,
                idempotency_hash,
            )
        return self._existing(existing, request_hash) if existing is not None else None

    @staticmethod
    def _existing(
        feedback: RCAFeedback,
        request_hash: str,
    ) -> RCAFeedbackView:
        if feedback.request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was used for another RCA feedback request"
            )
        return RCAFeedbackView.from_domain(
            feedback,
            is_duplicate=True,
        )

    @staticmethod
    def _sanitize_command(
        command: CreateRCAFeedbackCommand,
    ) -> CreateRCAFeedbackCommand:
        values: dict[str, str | None] = {}
        for name, value, maximum, multiline in (
            (
                "corrected_root_cause",
                command.corrected_root_cause,
                4096,
                True,
            ),
            ("follow_up_label", command.follow_up_label, 128, False),
            ("notes", command.notes, 4096, True),
        ):
            values[name] = _safe_optional_text(
                value,
                maximum=maximum,
                multiline=multiline,
            )
        return replace(command, **values)

    @staticmethod
    def _request_hash(command: CreateRCAFeedbackCommand) -> str:
        encoded = json.dumps(
            {
                "tenant_id": command.tenant_id,
                "workflow_run_id": command.workflow_run_id,
                "verdict": command.verdict.value,
                "corrected_root_cause": command.corrected_root_cause,
                "missing_evidence_types": [
                    item.value for item in command.missing_evidence_types
                ],
                "unsafe_recommendation_indexes": list(
                    command.unsafe_recommendation_indexes
                ),
                "follow_up_label": command.follow_up_label,
                "notes": command.notes,
                "requested_by": command.requested_by,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_audit_event(self, feedback: RCAFeedback) -> OutboxEvent:
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=feedback.tenant_id,
            aggregate_type="RCAFeedback",
            aggregate_id=feedback.feedback_id,
            event_type="rca.feedback.recorded",
            schema_version=1,
            payload={
                "feedback_id": feedback.feedback_id,
                "workflow_run_id": feedback.workflow_run_id,
                "report_id": feedback.report_id,
                "verdict": feedback.verdict.value,
                "missing_evidence_type_count": len(feedback.missing_evidence_types),
                "unsafe_recommendation_count": len(
                    feedback.unsafe_recommendation_indexes
                ),
                "created_by": feedback.created_by,
                "created_at": feedback.created_at.isoformat(),
            },
            occurred_at=feedback.created_at,
            trace_id=feedback.trace_id,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _safe_optional_text(
    value: str | None,
    *,
    maximum: int,
    multiline: bool,
) -> str | None:
    if value is None:
        return None
    normalized = (
        "\n".join(line.strip() for line in value.splitlines()).strip()
        if multiline
        else " ".join(value.split()).strip()
    )
    safe = redact_sensitive_text(normalized)[0][:maximum].strip()
    return safe or None
