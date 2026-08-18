from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.knowledge import (
    KnowledgeDocument,
    KnowledgeReviewStatus,
)
from devops_agent_platform.ports.knowledge import KnowledgeRetrieverPort


@dataclass(frozen=True)
class KnowledgeQuery:
    service: str
    alert_summary: str = ""
    error_fingerprint: str = ""
    log_keywords: tuple[str, ...] = ()
    trace_errors: tuple[str, ...] = ()
    recent_change_type: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.service, str) or not self.service.strip():
            raise AppValidationError("service is required for knowledge retrieval")

    @property
    def terms(self) -> tuple[str, ...]:
        raw = (
            self.service,
            self.alert_summary,
            self.error_fingerprint,
            *self.log_keywords,
            *self.trace_errors,
            self.recent_change_type,
        )
        terms: list[str] = []
        for value in raw:
            terms.extend(
                item.casefold() for item in re.findall(r"[a-zA-Z0-9_.:-]{2,}", value)
            )
        return tuple(dict.fromkeys(terms))


@dataclass(frozen=True)
class KnowledgeSearchResult:
    document: KnowledgeDocument
    score: float
    matched_terms: tuple[str, ...]
    is_historical_reference: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document.document_id,
            "title": self.document.title,
            "service_name": self.document.service_name,
            "document_type": self.document.document_type.value,
            "score": self.score,
            "matched_terms": list(self.matched_terms),
            "is_historical_reference": self.is_historical_reference,
            "review_status": self.document.review_status.value,
        }


class InMemoryKnowledgeRetriever:
    def __init__(self) -> None:
        self._documents: dict[tuple[str, str], KnowledgeDocument] = {}

    async def save(self, document: KnowledgeDocument) -> None:
        self._documents[(document.tenant_id, document.document_id)] = document

    async def get(self, tenant_id: str, document_id: str) -> KnowledgeDocument | None:
        return self._documents.get((tenant_id, document_id))

    async def search(
        self, tenant_id: str, query: KnowledgeQuery, *, top_k: int = 5
    ) -> tuple[KnowledgeSearchResult, ...]:
        if not isinstance(top_k, int) or not 1 <= top_k <= 50:
            raise AppValidationError("top_k must be between 1 and 50")
        terms = set(query.terms)
        results: list[KnowledgeSearchResult] = []
        for document in self._documents.values():
            if document.tenant_id != tenant_id or document.review_status not in {
                KnowledgeReviewStatus.PUBLISHED,
                KnowledgeReviewStatus.APPROVED,
            }:
                continue
            haystack = document.searchable_text
            matched = tuple(sorted(term for term in terms if term in haystack))
            if not matched:
                continue
            lexical = len(matched) / max(len(terms), 1)
            service_boost = (
                0.35
                if document.service_name.casefold() == query.service.casefold()
                else 0.0
            )
            fingerprint_boost = (
                0.35
                if query.error_fingerprint
                and document.error_fingerprint == query.error_fingerprint
                else 0.0
            )
            score = min(1.0, lexical * 0.5 + service_boost + fingerprint_boost)
            results.append(KnowledgeSearchResult(document, round(score, 6), matched))
        results.sort(key=lambda result: (-result.score, result.document.document_id))
        return tuple(results[:top_k])


class KnowledgeService:
    def __init__(self, retriever: KnowledgeRetrieverPort) -> None:
        self._retriever = retriever

    async def publish(self, document: KnowledgeDocument, now) -> KnowledgeDocument:
        published = document.publish(now)
        await self._retriever.save(published)
        return published

    async def search(
        self, tenant_id: str, query: KnowledgeQuery, *, top_k: int = 5
    ) -> tuple[KnowledgeSearchResult, ...]:
        return await self._retriever.search(tenant_id, query, top_k=top_k)


def build_knowledge_evidence_payload(result: KnowledgeSearchResult) -> dict[str, Any]:
    return {
        "evidence_type": "KNOWLEDGE",
        "source": "historical_knowledge",
        "document_id": result.document.document_id,
        "title": result.document.title,
        "score": result.score,
        "summary": "Historical reference only; not current incident fact.",
        "matched_terms": list(result.matched_terms),
    }
