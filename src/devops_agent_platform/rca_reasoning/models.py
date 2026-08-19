from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from devops_agent_platform.domain.enums import RootCauseType
from devops_agent_platform.domain.exceptions import AppValidationError


class ReasoningEvidenceType(StrEnum):
    METRIC = "METRIC"
    LOG = "LOG"
    TRACE = "TRACE"
    CHANGE = "CHANGE"
    TOPOLOGY = "TOPOLOGY"
    KNOWLEDGE = "KNOWLEDGE"
    RUNBOOK = "RUNBOOK"
    HTTP = "HTTP"

    @classmethod
    def from_value(cls, value: str) -> ReasoningEvidenceType:
        aliases = {
            "DEPLOYMENT": cls.CHANGE,
            "INCIDENT_HISTORY": cls.KNOWLEDGE,
        }
        normalized = value.strip().upper()
        return aliases.get(normalized, cls(normalized))


class ReasoningDecisionStatus(StrEnum):
    UNDETERMINED = "UNDETERMINED"
    CANDIDATE = "CANDIDATE"
    NO_ACTIONABLE_ROOT_CAUSE = "NO_ACTIONABLE_ROOT_CAUSE"


@dataclass(frozen=True)
class ReasoningEvidence:
    evidence_id: str
    evidence_type: ReasoningEvidenceType
    source: str
    summary: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("evidence_id", self.evidence_id, 128),
            ("source", self.source, 128),
            ("summary", self.summary, 4096),
        ):
            if not isinstance(value, str) or not 1 <= len(value) <= maximum:
                raise AppValidationError(f"{field_name} is invalid")
            if value != value.strip():
                raise AppValidationError(f"{field_name} has surrounding whitespace")
        if not isinstance(self.evidence_type, ReasoningEvidenceType):
            raise AppValidationError("evidence_type is invalid")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, int | float)
            or not isfinite(float(self.confidence))
            or not 0 <= float(self.confidence) <= 1
        ):
            raise AppValidationError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class NormalizedEvidence:
    evidence_id: str
    evidence_type: ReasoningEvidenceType
    source: str
    compact_fact: str
    inferred_type: RootCauseType
    service: str
    resource: str | None
    confidence: float
    contradicts_actionable_root_cause: bool = False
    historical_only: bool = False


@dataclass(frozen=True)
class RootCauseIdentity:
    service: str
    root_type: RootCauseType
    resource: str | None


@dataclass(frozen=True)
class RootCauseCandidate:
    candidate_id: str
    identity: RootCauseIdentity
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    source_evidence_types: tuple[ReasoningEvidenceType, ...]
    missing_evidence: tuple[str, ...]
    prior_score: float
    evidence_score: float
    causal_consistency: float
    contradiction_penalty: float
    missing_evidence_penalty: float
    final_score: float


@dataclass(frozen=True)
class RootCauseReasoningResult:
    normalized_evidence: tuple[NormalizedEvidence, ...]
    candidates: tuple[RootCauseCandidate, ...]
    recommended_status: ReasoningDecisionStatus
    calibrated_confidence: float
