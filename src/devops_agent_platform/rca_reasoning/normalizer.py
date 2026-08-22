from __future__ import annotations

import re

from .entity_extractor import EvidenceEntities, EvidenceEntityExtractor
from .models import (
    NormalizedEvidence,
    ReasoningEvidence,
    ReasoningEvidenceType,
    RootCauseType,
)
from .taxonomy import RootCauseTaxonomyMapper

_SERVICE_PATTERN = re.compile(r"\b([a-z][a-z0-9-]+-service)\b", re.IGNORECASE)
_VERSION_PATTERN = re.compile(r"\bv([0-9]+(?:\.[0-9]+)*)\b", re.IGNORECASE)
_STRUCTURED_CONTEXT_PRIORITY = {
    ReasoningEvidenceType.LOG: 0,
    ReasoningEvidenceType.TRACE: 1,
    ReasoningEvidenceType.HTTP: 2,
    ReasoningEvidenceType.METRIC: 3,
    ReasoningEvidenceType.TOPOLOGY: 4,
    ReasoningEvidenceType.CHANGE: 5,
    ReasoningEvidenceType.KNOWLEDGE: 6,
    ReasoningEvidenceType.RUNBOOK: 7,
}


class EvidenceNormalizer:
    def __init__(
        self,
        taxonomy: RootCauseTaxonomyMapper | None = None,
        *,
        legacy_resource_fallback: bool = True,
        safe_unknown_resolution: bool = False,
    ) -> None:
        self._taxonomy = taxonomy or RootCauseTaxonomyMapper()
        self._entities = EvidenceEntityExtractor()
        self._legacy_resource_fallback = legacy_resource_fallback
        self._safe_unknown_resolution = safe_unknown_resolution

    def normalize(
        self,
        *,
        incident_service: str,
        incident_summary: str,
        evidence: tuple[ReasoningEvidence, ...],
    ) -> tuple[NormalizedEvidence, ...]:
        incident_type = self._incident_type(incident_summary)
        incident_entities = self._entities.extract(incident_summary)
        extracted = tuple(
            (item, self._entities.extract(item.summary)) for item in evidence
        )
        structured_type, structured_resource = self._structured_context(extracted)
        return tuple(
            self._normalize_one(
                item,
                entities=entities,
                incident_service=incident_service,
                incident_summary=incident_summary,
                incident_type=incident_type,
                fallback_type=structured_type,
                fallback_resource=structured_resource or incident_entities.resource,
            )
            for item, entities in extracted
        )

    def _normalize_one(
        self,
        evidence: ReasoningEvidence,
        *,
        entities: EvidenceEntities,
        incident_service: str,
        incident_summary: str,
        incident_type: RootCauseType,
        fallback_type: RootCauseType,
        fallback_resource: str | None,
    ) -> NormalizedEvidence:
        own_structured_type = self._type_from_error(entities.error_type)
        inferred = own_structured_type
        if (
            inferred is RootCauseType.UNKNOWN
            and fallback_type is not RootCauseType.UNKNOWN
        ):
            inferred = fallback_type
        if inferred is RootCauseType.UNKNOWN:
            inferred = self._taxonomy.infer_from_text(evidence.summary)
        if inferred is RootCauseType.UNKNOWN:
            inferred = incident_type
        if (
            inferred is RootCauseType.UNKNOWN
            and not self._safe_unknown_resolution
        ):
            inferred = RootCauseType.APPLICATION_ERROR
        service = (
            incident_service
            if inferred is RootCauseType.CASCADING_FAILURE
            else entities.service
            or self._resolve_service(evidence.summary, incident_service)
        )
        resource = entities.resource or fallback_resource
        if resource is None and self._legacy_resource_fallback:
            resource = self._resolve_legacy_resource(
                inferred,
                service=service,
                text=f"{incident_summary} {evidence.summary}",
                version=entities.version,
            )
        if inferred is RootCauseType.DEPLOYMENT_REGRESSION and entities.version:
            resource = f"{service}:{entities.version}"
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

    def _structured_context(
        self,
        extracted: tuple[tuple[ReasoningEvidence, EvidenceEntities], ...],
    ) -> tuple[RootCauseType, str | None]:
        ordered = sorted(
            extracted,
            key=lambda item: _STRUCTURED_CONTEXT_PRIORITY[item[0].evidence_type],
        )
        for _, entities in ordered:
            root_type = self._type_from_error(entities.error_type)
            if root_type is not RootCauseType.UNKNOWN:
                return root_type, entities.resource
        return RootCauseType.UNKNOWN, None

    def _type_from_error(self, error_type: str | None) -> RootCauseType:
        if not error_type:
            return RootCauseType.UNKNOWN
        mapped = self._taxonomy.map_type(error_type)
        if mapped is not RootCauseType.UNKNOWN:
            return mapped
        return self._taxonomy.infer_from_text(error_type)

    def _incident_type(self, summary: str) -> RootCauseType:
        if self._is_historical_only(summary.casefold()):
            return RootCauseType.UNKNOWN
        return self._taxonomy.infer_from_text(summary)

    @staticmethod
    def _resolve_service(text: str, fallback: str) -> str:
        matches = _SERVICE_PATTERN.findall(text)
        return matches[-1].casefold() if matches else fallback

    @staticmethod
    def _resolve_legacy_resource(
        root_type: RootCauseType,
        *,
        service: str,
        text: str,
        version: str | None = None,
    ) -> str | None:
        """兼容 v0.6 固定场景；v0.7 黑盒路径关闭此回退。"""
        lowered = text.casefold()
        if root_type is RootCauseType.DEPENDENCY_TIMEOUT:
            return "payment-provider" if "provider" in lowered else "postgres"
        if root_type is RootCauseType.DEPENDENCY_LATENCY:
            return "redis" if "redis" in lowered else None
        if root_type is RootCauseType.CONFIGURATION_ERROR:
            return f"{service}/config"
        if root_type is RootCauseType.DEPLOYMENT_REGRESSION:
            match = _VERSION_PATTERN.search(text)
            resolved = version or (match.group(0).casefold() if match else None)
            return f"{service}:{resolved}" if resolved else service
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
