import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime

from devops_agent_platform.domain.enums import (
    TicketDecision,
    TicketDraftStatus,
    TicketPriority,
)
from devops_agent_platform.domain.exceptions import AppValidationError

MAX_TICKET_DESCRIPTION_BYTES = 24 * 1024


@dataclass(frozen=True)
class TicketDraft:
    """由可信 RCA 报告派生的不可变本地工单草稿。"""

    ticket_draft_id: str
    tenant_id: str
    incident_id: str
    workflow_run_id: str
    report_id: str
    status: TicketDraftStatus
    priority: TicketPriority
    title: str
    description: str
    evidence_ids: tuple[str, ...]
    recommendations: tuple[str, ...]
    created_by: str
    idempotency_key_hash: str
    request_hash: str
    trace_id: str
    created_at: datetime
    version: int = 1
    decided_by: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    decision_idempotency_key_hash: str | None = None
    decision_request_hash: str | None = None
    decision_trace_id: str | None = None

    def __post_init__(self) -> None:
        """校验来源身份、内容容量、幂等摘要和创建时间。"""
        for field_name, value, maximum in (
            ("ticket_draft_id", self.ticket_draft_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("incident_id", self.incident_id, 64),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("report_id", self.report_id, 64),
            ("title", self.title, 256),
            ("created_by", self.created_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            self._validate_text(field_name, value, maximum)
        if not isinstance(self.status, TicketDraftStatus):
            raise AppValidationError("status must be a TicketDraftStatus")
        if not isinstance(self.priority, TicketPriority):
            raise AppValidationError("priority must be a TicketPriority")
        self._validate_description()
        self._validate_string_tuple(
            "evidence_ids",
            self.evidence_ids,
            minimum=1,
            maximum=100,
            item_maximum=64,
        )
        self._validate_string_tuple(
            "recommendations",
            self.recommendations,
            minimum=0,
            maximum=20,
            item_maximum=1024,
        )
        self._validate_hash(
            "idempotency_key_hash",
            self.idempotency_key_hash,
        )
        self._validate_hash("request_hash", self.request_hash)
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise AppValidationError("created_at must include timezone information")
        self._validate_decision_state()

    def decide(
        self,
        decision: TicketDecision,
        *,
        decided_by: str,
        reason: str | None,
        decided_at: datetime,
        idempotency_key_hash: str,
        request_hash: str,
        trace_id: str,
    ) -> "TicketDraft":
        """从草稿单向进入批准或拒绝终态。"""
        if self.status is not TicketDraftStatus.DRAFT:
            raise AppValidationError("only draft tickets can receive a decision")
        if not isinstance(decision, TicketDecision):
            raise AppValidationError("decision must be a TicketDecision")
        status = (
            TicketDraftStatus.APPROVED
            if decision is TicketDecision.APPROVE
            else TicketDraftStatus.REJECTED
        )
        return replace(
            self,
            status=status,
            version=2,
            decided_by=decided_by,
            decision_reason=reason,
            decided_at=decided_at,
            decision_idempotency_key_hash=idempotency_key_hash,
            decision_request_hash=request_hash,
            decision_trace_id=trace_id,
        )

    def _validate_decision_state(self) -> None:
        """保证状态、版本和人工确认字段同步变化。"""
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version not in {1, 2}
        ):
            raise AppValidationError("ticket draft version must be 1 or 2")
        decision_fields = (
            self.decided_by,
            self.decision_reason,
            self.decided_at,
            self.decision_idempotency_key_hash,
            self.decision_request_hash,
            self.decision_trace_id,
        )
        if self.status is TicketDraftStatus.DRAFT:
            if self.version != 1 or any(value is not None for value in decision_fields):
                raise AppValidationError(
                    "draft ticket must not contain decision fields"
                )
            return
        if (
            self.version != 2
            or self.decided_by is None
            or self.decided_at is None
            or self.decision_idempotency_key_hash is None
            or self.decision_request_hash is None
            or self.decision_trace_id is None
        ):
            raise AppValidationError("decided ticket requires complete decision fields")
        self._validate_text("decided_by", self.decided_by, 128)
        if (
            self.decided_at.tzinfo is None
            or self.decided_at.utcoffset() is None
            or self.decided_at < self.created_at
        ):
            raise AppValidationError("decided_at is invalid")
        self._validate_hash(
            "decision_idempotency_key_hash",
            self.decision_idempotency_key_hash,
        )
        self._validate_hash(
            "decision_request_hash",
            self.decision_request_hash,
        )
        self._validate_text(
            "decision_trace_id",
            self.decision_trace_id,
            128,
        )
        if self.status is TicketDraftStatus.APPROVED:
            if self.decision_reason is not None:
                raise AppValidationError(
                    "approved ticket must not contain a rejection reason"
                )
        elif self.status is TicketDraftStatus.REJECTED:
            if self.decision_reason is None:
                raise AppValidationError("rejected ticket requires a reason")
            self._validate_text(
                "decision_reason",
                self.decision_reason,
                2048,
                multiline=True,
            )

    def _validate_description(self) -> None:
        """限制描述的 UTF-8 实际大小，避免多字节文本绕过容量。"""
        self._validate_text(
            "description",
            self.description,
            16_384,
            multiline=True,
        )
        if len(self.description.encode("utf-8")) > MAX_TICKET_DESCRIPTION_BYTES:
            raise AppValidationError("description is too large")

    @classmethod
    def _validate_string_tuple(
        cls,
        field_name: str,
        value: tuple[str, ...],
        *,
        minimum: int,
        maximum: int,
        item_maximum: int,
    ) -> None:
        """校验不可变字符串集合、唯一性和单项容量。"""
        if (
            not isinstance(value, tuple)
            or not minimum <= len(value) <= maximum
            or len(value) != len(set(value))
        ):
            raise AppValidationError(f"{field_name} is invalid")
        for item in value:
            cls._validate_text(field_name, item, item_maximum)

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
        *,
        multiline: bool = False,
    ) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(
                (ord(character) < 32 and not (multiline and character == "\n"))
                or ord(character) == 127
                for character in value
            )
        ):
            raise AppValidationError(f"{field_name} is invalid")

    @staticmethod
    def _validate_hash(field_name: str, value: str) -> None:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise AppValidationError(f"{field_name} must be a SHA-256 hex digest")


def build_ticket_request_hash(
    tenant_id: str,
    workflow_run_id: str,
    created_by: str,
) -> str:
    """为创建意图生成稳定指纹，区分幂等重放与键误复用。"""
    encoded = json.dumps(
        {
            "tenant_id": tenant_id,
            "workflow_run_id": workflow_run_id,
            "created_by": created_by,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
