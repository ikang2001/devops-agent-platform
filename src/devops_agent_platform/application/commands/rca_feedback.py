from dataclasses import dataclass

from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAFeedbackVerdict,
)
from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class CreateRCAFeedbackCommand:
    """创建一次不可变人工复核记录的应用命令。"""

    tenant_id: str
    workflow_run_id: str
    verdict: RCAFeedbackVerdict
    corrected_root_cause: str | None
    missing_evidence_types: tuple[EvidenceType, ...]
    unsafe_recommendation_indexes: tuple[int, ...]
    follow_up_label: str | None
    notes: str | None
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("idempotency_key", self.idempotency_key, 256),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{name} is invalid")
