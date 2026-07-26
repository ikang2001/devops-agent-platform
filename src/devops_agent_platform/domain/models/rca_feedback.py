from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAFeedbackVerdict,
)
from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class RCAFeedback:
    """一次不可变的 RCA 人工复核记录。"""

    feedback_id: str
    tenant_id: str
    workflow_run_id: str
    report_id: str
    verdict: RCAFeedbackVerdict
    corrected_root_cause: str | None
    missing_evidence_types: tuple[EvidenceType, ...]
    unsafe_recommendation_indexes: tuple[int, ...]
    follow_up_label: str | None
    notes: str | None
    created_by: str
    idempotency_key_hash: str
    request_hash: str
    trace_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        """拒绝歧义身份、无意义反馈和无时区审计时间。"""
        for name, value, maximum in (
            ("feedback_id", self.feedback_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("report_id", self.report_id, 64),
            ("created_by", self.created_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            _validate_text(name, value, maximum)
        _validate_hash("idempotency_key_hash", self.idempotency_key_hash)
        _validate_hash("request_hash", self.request_hash)
        if not isinstance(self.verdict, RCAFeedbackVerdict):
            raise AppValidationError("verdict is invalid")
        _validate_optional_text(
            "corrected_root_cause",
            self.corrected_root_cause,
            4096,
        )
        _validate_optional_text(
            "follow_up_label",
            self.follow_up_label,
            128,
            multiline=False,
        )
        _validate_optional_text("notes", self.notes, 4096)
        if (
            not isinstance(self.missing_evidence_types, tuple)
            or not all(
                isinstance(item, EvidenceType) for item in self.missing_evidence_types
            )
            or len(set(self.missing_evidence_types)) != len(self.missing_evidence_types)
        ):
            raise AppValidationError("missing_evidence_types is invalid")
        if (
            not isinstance(self.unsafe_recommendation_indexes, tuple)
            or not all(
                isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 99
                for item in self.unsafe_recommendation_indexes
            )
            or tuple(sorted(set(self.unsafe_recommendation_indexes)))
            != self.unsafe_recommendation_indexes
        ):
            raise AppValidationError("unsafe_recommendation_indexes is invalid")
        if self.verdict is RCAFeedbackVerdict.ACCEPTED and (
            self.corrected_root_cause is not None
            or self.missing_evidence_types
            or self.unsafe_recommendation_indexes
        ):
            raise AppValidationError(
                "accepted feedback cannot include correction findings"
            )
        if self.verdict is not RCAFeedbackVerdict.ACCEPTED and not any(
            (
                self.corrected_root_cause,
                self.missing_evidence_types,
                self.unsafe_recommendation_indexes,
                self.notes,
            )
        ):
            raise AppValidationError(
                "non-accepted feedback requires at least one finding"
            )
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise AppValidationError("created_at must be timezone-aware")


def _validate_text(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")


def _validate_optional_text(
    name: str,
    value: str | None,
    maximum: int,
    *,
    multiline: bool = True,
) -> None:
    if value is None:
        return
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
        raise AppValidationError(f"{name} is invalid")


def _validate_hash(name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")
