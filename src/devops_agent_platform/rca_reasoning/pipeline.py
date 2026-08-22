from __future__ import annotations

import hashlib
from collections import defaultdict

from .models import (
    NormalizedEvidence,
    ReasoningDecisionStatus,
    ReasoningEvidence,
    ReasoningEvidenceType,
    RootCauseCandidate,
    RootCauseIdentity,
    RootCauseReasoningResult,
    RootCauseType,
)
from .normalizer import EvidenceNormalizer

_EVIDENCE_WEIGHTS = {
    ReasoningEvidenceType.LOG: 1.0,
    ReasoningEvidenceType.TRACE: 0.95,
    ReasoningEvidenceType.METRIC: 0.8,
    ReasoningEvidenceType.HTTP: 0.75,
    ReasoningEvidenceType.TOPOLOGY: 0.6,
    ReasoningEvidenceType.CHANGE: 0.55,
    ReasoningEvidenceType.KNOWLEDGE: 0.4,
    ReasoningEvidenceType.RUNBOOK: 0.2,
}
_DIRECT_TYPES = {
    ReasoningEvidenceType.LOG,
    ReasoningEvidenceType.TRACE,
    ReasoningEvidenceType.METRIC,
    ReasoningEvidenceType.HTTP,
}


class RootCauseReasoningPipeline:
    """在调用 LLM 前生成有限、可解释且确定性排序的候选。"""

    def __init__(
        self,
        normalizer: EvidenceNormalizer | None = None,
        *,
        strict_entity_resolution: bool = False,
        safe_unknown_resolution: bool = False,
    ) -> None:
        self._normalizer = normalizer or EvidenceNormalizer(
            legacy_resource_fallback=not strict_entity_resolution,
            safe_unknown_resolution=safe_unknown_resolution,
        )
        self._safe_unknown_resolution = safe_unknown_resolution

    def reason(
        self,
        *,
        incident_service: str,
        incident_summary: str,
        evidence: tuple[ReasoningEvidence, ...],
        max_candidates: int = 3,
    ) -> RootCauseReasoningResult:
        if not 1 <= max_candidates <= 5:
            raise ValueError("max_candidates must be between 1 and 5")
        normalized = self._normalizer.normalize(
            incident_service=incident_service,
            incident_summary=incident_summary,
            evidence=evidence,
        )
        candidates = self._build_candidates(normalized)[:max_candidates]
        status, confidence = self._recommend(candidates)
        return RootCauseReasoningResult(
            normalized_evidence=normalized,
            candidates=candidates,
            recommended_status=status,
            calibrated_confidence=confidence,
        )

    def _build_candidates(
        self,
        evidence: tuple[NormalizedEvidence, ...],
    ) -> tuple[RootCauseCandidate, ...]:
        grouped: dict[RootCauseIdentity, list[NormalizedEvidence]] = defaultdict(list)
        for item in evidence:
            if (
                self._safe_unknown_resolution
                and item.inferred_type is RootCauseType.UNKNOWN
            ):
                continue
            identity = RootCauseIdentity(
                service=item.service,
                root_type=item.inferred_type,
                resource=item.resource,
            )
            grouped[identity].append(item)

        negative_ids = tuple(
            item.evidence_id
            for item in evidence
            if item.contradicts_actionable_root_cause
        )
        candidates = tuple(
            self._score_candidate(identity, items, negative_ids)
            for identity, items in grouped.items()
        )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (-item.final_score, item.candidate_id),
            )
        )

    def _score_candidate(
        self,
        identity: RootCauseIdentity,
        evidence: list[NormalizedEvidence],
        negative_ids: tuple[str, ...],
    ) -> RootCauseCandidate:
        is_no_action = identity.root_type is RootCauseType.NO_ACTIONABLE_ROOT_CAUSE
        support_items = (
            evidence
            if is_no_action
            else [
                item
                for item in evidence
                if not item.contradicts_actionable_root_cause
            ]
        )
        support = tuple(dict.fromkeys(item.evidence_id for item in support_items))
        sources = tuple(
            sorted(
                {item.evidence_type for item in support_items},
                key=lambda item: item.value,
            )
        )
        contradictions = () if is_no_action else tuple(
            evidence_id for evidence_id in negative_ids if evidence_id not in support
        )
        evidence_score = min(
            sum(
                _EVIDENCE_WEIGHTS[item.evidence_type] * item.confidence
                for item in support_items
                if not item.historical_only
            )
            / 2,
            0.75,
        )
        causal_consistency = self._causal_consistency(identity, sources)
        missing = self._missing_evidence(identity, sources)
        contradiction_penalty = min(len(contradictions) * 0.2, 0.6)
        missing_penalty = min(len(missing) * 0.15, 0.45)
        prior = 0.1
        final = max(
            0.0,
            min(
                1.0,
                prior
                + evidence_score
                + causal_consistency
                - contradiction_penalty
                - missing_penalty,
            ),
        )
        return RootCauseCandidate(
            candidate_id=_candidate_id(identity),
            identity=identity,
            supporting_evidence_ids=support,
            contradicting_evidence_ids=contradictions,
            source_evidence_types=sources,
            missing_evidence=missing,
            prior_score=prior,
            evidence_score=evidence_score,
            causal_consistency=causal_consistency,
            contradiction_penalty=contradiction_penalty,
            missing_evidence_penalty=missing_penalty,
            final_score=round(final, 6),
        )

    @staticmethod
    def _causal_consistency(
        identity: RootCauseIdentity,
        sources: tuple[ReasoningEvidenceType, ...],
    ) -> float:
        source_set = set(sources)
        if identity.root_type is RootCauseType.DEPLOYMENT_REGRESSION:
            return 0.15 if ReasoningEvidenceType.CHANGE in source_set and (
                source_set & _DIRECT_TYPES
            ) else 0.0
        if identity.root_type in {
            RootCauseType.DEPENDENCY_LATENCY,
            RootCauseType.DEPENDENCY_TIMEOUT,
        }:
            return 0.1 if ReasoningEvidenceType.TRACE in source_set else 0.0
        if identity.root_type is RootCauseType.NO_ACTIONABLE_ROOT_CAUSE:
            return 0.1 if len(source_set) >= 2 else 0.05
        return 0.05 if source_set & _DIRECT_TYPES else 0.0

    @staticmethod
    def _missing_evidence(
        identity: RootCauseIdentity,
        sources: tuple[ReasoningEvidenceType, ...],
    ) -> tuple[str, ...]:
        source_set = set(sources)
        missing: list[str] = []
        if identity.root_type is RootCauseType.DEPLOYMENT_REGRESSION:
            if ReasoningEvidenceType.CHANGE not in source_set:
                missing.append("recent_change")
            if not source_set & _DIRECT_TYPES:
                missing.append("post_change_signal_shift")
        if identity.root_type in {
            RootCauseType.DEPENDENCY_LATENCY,
            RootCauseType.DEPENDENCY_TIMEOUT,
        } and ReasoningEvidenceType.TRACE not in source_set:
            missing.append("dependency_trace")
        return tuple(missing)

    @staticmethod
    def _recommend(
        candidates: tuple[RootCauseCandidate, ...],
    ) -> tuple[ReasoningDecisionStatus, float]:
        if not candidates or candidates[0].final_score < 0.5:
            return ReasoningDecisionStatus.UNDETERMINED, 0.0
        top = candidates[0]
        confidence = min(top.final_score, 0.9)
        if top.contradicting_evidence_ids:
            confidence = min(confidence, 0.65)
        if top.identity.root_type is RootCauseType.NO_ACTIONABLE_ROOT_CAUSE:
            return ReasoningDecisionStatus.NO_ACTIONABLE_ROOT_CAUSE, confidence
        return ReasoningDecisionStatus.CANDIDATE, confidence


def _candidate_id(identity: RootCauseIdentity) -> str:
    raw = f"{identity.service}|{identity.root_type.value}|{identity.resource or ''}"
    return "cand-" + hashlib.sha256(raw.encode()).hexdigest()[:12]
