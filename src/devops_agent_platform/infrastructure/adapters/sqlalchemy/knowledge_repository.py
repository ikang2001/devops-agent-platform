from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.application.services.knowledge_service import (
    KnowledgeQuery,
    KnowledgeSearchResult,
)
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.domain.models.knowledge import (
    KnowledgeDocument,
    KnowledgeDocumentType,
    KnowledgeReviewStatus,
)
from devops_agent_platform.infrastructure.database.models.knowledge import (
    KnowledgeDocumentRecord,
)


class SQLAlchemyKnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, document: KnowledgeDocument) -> None:
        record = KnowledgeDocumentRecord(
            document_id=document.document_id,
            tenant_id=document.tenant_id,
            document_type=document.document_type.value,
            title=document.title,
            service_name=document.service_name,
            body=document.body,
            source_incident_id=document.source_incident_id,
            error_fingerprint=document.error_fingerprint,
            version=document.version,
            review_status=document.review_status.value,
            created_at=document.created_at,
            published_at=document.published_at,
            metadata_json=json.dumps(document.metadata, ensure_ascii=False),
        )
        try:
            self._session.add(record)
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("knowledge document persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("could not persist knowledge document") from exc

    async def get(self, tenant_id: str, document_id: str) -> KnowledgeDocument | None:
        statement = select(KnowledgeDocumentRecord).where(
            KnowledgeDocumentRecord.tenant_id == tenant_id,
            KnowledgeDocumentRecord.document_id == document_id,
        )
        try:
            record = await self._session.scalar(statement)
        except SQLAlchemyError as exc:
            raise PersistenceError("could not load knowledge document") from exc
        return _to_domain(record) if record is not None else None

    async def search(
        self,
        tenant_id: str,
        query: KnowledgeQuery,
        *,
        top_k: int = 5,
    ) -> tuple[KnowledgeSearchResult, ...]:
        if not isinstance(top_k, int) or not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        statement = select(KnowledgeDocumentRecord).where(
            KnowledgeDocumentRecord.tenant_id == tenant_id,
            KnowledgeDocumentRecord.review_status.in_(
                [
                    KnowledgeReviewStatus.PUBLISHED.value,
                    KnowledgeReviewStatus.APPROVED.value,
                ]
            ),
        )
        try:
            records = (await self._session.scalars(statement)).all()
        except SQLAlchemyError as exc:
            raise PersistenceError("could not search knowledge documents") from exc
        terms = set(query.terms)
        results: list[KnowledgeSearchResult] = []
        for record in records:
            document = _to_domain(record)
            haystack = document.searchable_text
            matched = tuple(
                sorted(
                    term
                    for term in terms
                    if term in haystack
                    or any(
                        term in token
                        for token in re.findall(r"[a-z0-9_.:-]+", haystack)
                    )
                )
            )
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


class SQLAlchemyKnowledgeRetriever:
    """以 Session 工厂为边界的只读知识检索适配器。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, document: KnowledgeDocument) -> None:
        async with self._session_factory() as session:
            await SQLAlchemyKnowledgeRepository(session).save(document)
            await session.commit()

    async def search(
        self,
        tenant_id: str,
        query: KnowledgeQuery,
        *,
        top_k: int = 5,
    ) -> tuple[KnowledgeSearchResult, ...]:
        async with self._session_factory() as session:
            return await SQLAlchemyKnowledgeRepository(session).search(
                tenant_id, query, top_k=top_k
            )


def _to_domain(record: KnowledgeDocumentRecord) -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id=record.document_id,
        tenant_id=record.tenant_id,
        document_type=KnowledgeDocumentType(record.document_type),
        title=record.title,
        service_name=record.service_name,
        body=record.body,
        source_incident_id=record.source_incident_id,
        error_fingerprint=record.error_fingerprint,
        version=record.version,
        review_status=KnowledgeReviewStatus(record.review_status),
        created_at=record.created_at,
        published_at=record.published_at,
        metadata=tuple(
            tuple(item) for item in json.loads(record.metadata_json or "[]")
        ),
    )
