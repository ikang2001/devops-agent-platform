from __future__ import annotations

import re

from .models import (
    NormalizedEvidence,
    ReasoningEvidence,
    RootCauseType,
)
from .taxonomy import RootCauseTaxonomyMapper

_SERVICE_PATTERN = re.compile(r"\b([a-z][a-z0-9-]+-service)\b", re.IGNORECASE)
_VERSION_PATTERN = re.compile(r"\bv([0-9]+(?:\.[0-9]+)*)\b", re.IGNORECASE)


class EvidenceNormalizer:
    def __init__(self, taxonomy: RootCauseTaxonomyMapper | None = None) -> None:
        self._taxonomy = taxonomy or RootCauseTaxonomyMapper()

    def normalize(
        self,
        *,
        incident_service: str,
        incident_summary: str,
        evidence: tuple[ReasoningEvidence, ...],
    ) -> tuple[NormalizedEvidence, ...]:
        incident_type = self._incident_type(incident_summary)
        return tuple(
            self._normalize_one(
                item,
                incident_service=incident_service,
                incident_summary=incident_summary,
                incident_type=incident_type,
            )
            for item in evidence
        )

    def _normalize_one(
        self,
        evidence: ReasoningEvidence,
        *,
        incident_service: str,
        incident_summary: str,
        incident_type: RootCauseType,
    ) -> NormalizedEvidence:
        inferred = self._taxonomy.infer_from_text(evidence.summary)
        if inferred is RootCauseType.UNKNOWN:
            inferred = incident_type
        if inferred is RootCauseType.UNKNOWN:
            inferred = RootCauseType.APPLICATION_ERROR
        service = (
            incident_service
            if inferred is RootCauseType.CASCADING_FAILURE
            else self._resolve_service(evidence.summary, incident_service)
        )
        resource = self._resolve_resource(
            inferred,
            service=service,
            text=f"{incident_summary} {evidence.summary}",
        )
        lowered = evidence.summary.casefold()
        return NormalizedEvidence(
            evidence_id=evidence.evidence_id,
            evidence_type=evidence.evidence_type,
            source=evidence.source,
            compact_fact=" ".join(evidence.summary.split()),
            inferred_type=inferred,
            service=service,
            resource=resource,
            confidence=float(evidence.confidence),
            contradicts_actionable_root_cause=self._is_negative_fact(lowered),
            historical_only=self._is_historical_only(lowered),
        )

    def _incident_type(self, summary: str) -> RootCauseType:
        if self._is_historical_only(summary.casefold()):
            return RootCauseType.UNKNOWN
        return self._taxonomy.infer_from_text(summary)

    @staticmethod
    def _resolve_service(text: str, fallback: str) -> str:
        matches = _SERVICE_PATTERN.findall(text)
        return matches[-1].casefold() if matches else fallback

    @staticmethod
    def _resolve_resource(
        root_type: RootCauseType,
        *,
        service: str,
        text: str,
    ) -> str | None:
        lowered = text.casefold()
        if root_type is RootCauseType.DEPENDENCY_TIMEOUT:
            return "payment-provider" if "provider" in lowered else "postgres"
        if root_type is RootCauseType.DEPENDENCY_LATENCY:
            return "redis" if "redis" in lowered else None
        if root_type is RootCauseType.CONFIGURATION_ERROR:
            return f"{service}/config"
        if root_type is RootCauseType.DEPLOYMENT_REGRESSION:
            version = _VERSION_PATTERN.search(text)
            return f"{service}:{version.group(0).casefold()}" if version else service
        if root_type is RootCauseType.RESOURCE_EXHAUSTION:
            return f"{service.removesuffix('-service')}-db-pool"
        if root_type is RootCauseType.LATENCY:
            return f"{service}:latency-fault"
        if root_type in {RootCauseType.CASCADING_FAILURE, RootCauseType.KNOWN_ERROR}:
            return service
        if root_type is RootCauseType.NO_ACTIONABLE_ROOT_CAUSE:
            return "alert-rule"
        if root_type is RootCauseType.APPLICATION_ERROR and "resembles old" in lowered:
            return service
        return None

    @staticmethod
    def _is_negative_fact(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "no customer impact",
                "no failure counter",
                "remains successful",
                "downstream spans remain successful",
                "pre-existing error",
                "error existed before",
            )
        )

    @staticmethod
    def _is_historical_only(text: str) -> bool:
        return any(
            phrase in text
            for phrase in ("resembles old", "historical only", "previous incident")
        )
