from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    PermissionDenied,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.dataset_release import (
    DatasetRelease,
    DatasetReleaseReview,
    DatasetReleaseStatus,
    DatasetReviewRole,
)
from devops_agent_platform.infrastructure.database.mappers.outbox import (
    OutboxEventMapper,
)
from devops_agent_platform.infrastructure.database.models.dataset_release import (
    DatasetReleaseRecord,
    DatasetReleaseReviewRecord,
)


class SQLAlchemyDatasetReleaseStore:
    """持久化在线双审核数据集发布状态机及同事务审计。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        release: DatasetRelease,
        *,
        idempotency_key_hash: str,
        request_hash: str,
        audit_event: OutboxEvent,
    ) -> tuple[DatasetRelease, bool]:
        self._validate_audit(release.tenant_id, release.release_id, audit_event)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    existing = await self._load_record(
                        session, release.tenant_id, release.release_id, lock=True
                    )
                    if existing is not None:
                        if (
                            existing.create_idempotency_hash == idempotency_key_hash
                            and existing.create_request_hash == request_hash
                        ):
                            return await self._to_domain(session, existing), True
                        raise ConflictError("dataset release already exists")
                    record = DatasetReleaseRecord(
                        tenant_id=release.tenant_id,
                        release_id=release.release_id,
                        dataset_id=release.dataset_id,
                        source_version=release.source_version,
                        version=release.version,
                        dataset_json=release.dataset_json,
                        dataset_sha256=release.dataset_sha256,
                        candidate_sha256=release.candidate_sha256,
                        curation_review_sha256=release.curation_review_sha256,
                        requested_by=release.requested_by,
                        synthetic=release.synthetic,
                        status=release.status.value,
                        revision=release.revision,
                        create_idempotency_hash=idempotency_key_hash,
                        create_request_hash=request_hash,
                        created_at=release.created_at,
                    )
                    session.add(record)
                    session.add(OutboxEventMapper.to_record(audit_event))
                    await session.flush()
                    return release, False
        except IntegrityError as exc:
            raise ConflictError("dataset release persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("could not create dataset release") from exc

    async def add_review(
        self,
        tenant_id: str,
        release_id: str,
        review: DatasetReleaseReview,
        *,
        expected_revision: int,
        idempotency_key_hash: str,
        request_hash: str,
        audit_event: OutboxEvent,
    ) -> tuple[DatasetRelease, bool]:
        self._validate_audit(tenant_id, release_id, audit_event)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await self._required_record(session, tenant_id, release_id)
                    duplicate = await session.scalar(
                        select(DatasetReleaseReviewRecord).where(
                            DatasetReleaseReviewRecord.tenant_id == tenant_id,
                            DatasetReleaseReviewRecord.release_id == release_id,
                            DatasetReleaseReviewRecord.idempotency_key_hash
                            == idempotency_key_hash,
                        )
                    )
                    if duplicate is not None:
                        if duplicate.request_hash != request_hash:
                            raise ConflictError(
                                "idempotency key was used for another dataset review"
                            )
                        return await self._to_domain(session, record), True
                    if record.status != DatasetReleaseStatus.DRAFT.value:
                        raise ConflictError("published dataset release is immutable")
                    if record.revision != expected_revision:
                        raise ConflictError("dataset release revision conflict")
                    if review.reviewer_id == record.requested_by:
                        raise PermissionDenied(
                            "dataset release requester cannot self-review"
                        )
                    session.add(
                        DatasetReleaseReviewRecord(
                            tenant_id=tenant_id,
                            release_id=release_id,
                            reviewer_id=review.reviewer_id,
                            role=review.role.value,
                            notes=review.notes,
                            idempotency_key_hash=idempotency_key_hash,
                            request_hash=request_hash,
                            result_revision=record.revision + 1,
                            reviewed_at=review.reviewed_at,
                        )
                    )
                    record.revision += 1
                    session.add(OutboxEventMapper.to_record(audit_event))
                    await session.flush()
                    return await self._to_domain(session, record), False
        except IntegrityError as exc:
            raise ConflictError(
                "dataset review role or reviewer already exists"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("could not review dataset release") from exc

    async def publish(
        self,
        tenant_id: str,
        release_id: str,
        *,
        expected_revision: int,
        published_at: datetime,
        idempotency_key_hash: str,
        request_hash: str,
        audit_event: OutboxEvent,
    ) -> tuple[DatasetRelease, bool]:
        self._validate_audit(tenant_id, release_id, audit_event)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await self._required_record(session, tenant_id, release_id)
                    if record.status == DatasetReleaseStatus.PUBLISHED.value:
                        if (
                            record.publish_idempotency_hash == idempotency_key_hash
                            and record.publish_request_hash == request_hash
                        ):
                            return await self._to_domain(session, record), True
                        raise ConflictError("published dataset release is immutable")
                    if record.revision != expected_revision:
                        raise ConflictError("dataset release revision conflict")
                    reviews = await self._load_reviews(session, tenant_id, release_id)
                    if {item.role for item in reviews} != {
                        DatasetReviewRole.DOMAIN,
                        DatasetReviewRole.PRIVACY,
                    }:
                        raise ConflictError(
                            "dataset release review quorum is incomplete"
                        )
                    record.status = DatasetReleaseStatus.PUBLISHED.value
                    record.revision += 1
                    record.published_at = published_at
                    record.publish_idempotency_hash = idempotency_key_hash
                    record.publish_request_hash = request_hash
                    session.add(OutboxEventMapper.to_record(audit_event))
                    await session.flush()
                    return await self._to_domain(session, record), False
        except SQLAlchemyError as exc:
            raise PersistenceError("could not publish dataset release") from exc

    async def get(self, tenant_id: str, release_id: str) -> DatasetRelease | None:
        try:
            async with self._session_factory() as session:
                record = await self._load_record(session, tenant_id, release_id)
                return (
                    await self._to_domain(session, record)
                    if record is not None
                    else None
                )
        except SQLAlchemyError as exc:
            raise PersistenceError("could not load dataset release") from exc

    async def _required_record(
        self,
        session: AsyncSession,
        tenant_id: str,
        release_id: str,
    ) -> DatasetReleaseRecord:
        record = await self._load_record(session, tenant_id, release_id, lock=True)
        if record is None:
            raise ResourceNotFound("dataset release not found")
        return record

    @staticmethod
    async def _load_record(
        session: AsyncSession,
        tenant_id: str,
        release_id: str,
        *,
        lock: bool = False,
    ) -> DatasetReleaseRecord | None:
        statement = select(DatasetReleaseRecord).where(
            DatasetReleaseRecord.tenant_id == tenant_id,
            DatasetReleaseRecord.release_id == release_id,
        )
        if lock:
            statement = statement.with_for_update()
        return await session.scalar(statement.limit(1))

    async def _to_domain(
        self,
        session: AsyncSession,
        record: DatasetReleaseRecord,
    ) -> DatasetRelease:
        reviews = await self._load_reviews(
            session,
            record.tenant_id,
            record.release_id,
        )
        try:
            return DatasetRelease(
                release_id=record.release_id,
                tenant_id=record.tenant_id,
                dataset_id=record.dataset_id,
                source_version=record.source_version,
                version=record.version,
                dataset_json=record.dataset_json,
                dataset_sha256=record.dataset_sha256,
                candidate_sha256=record.candidate_sha256,
                curation_review_sha256=record.curation_review_sha256,
                requested_by=record.requested_by,
                synthetic=record.synthetic,
                status=DatasetReleaseStatus(record.status),
                revision=record.revision,
                reviews=reviews,
                created_at=record.created_at,
                published_at=record.published_at,
            )
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError("stored dataset release is invalid") from exc

    @staticmethod
    async def _load_reviews(
        session: AsyncSession,
        tenant_id: str,
        release_id: str,
    ) -> tuple[DatasetReleaseReview, ...]:
        records = (
            await session.scalars(
                select(DatasetReleaseReviewRecord)
                .where(
                    DatasetReleaseReviewRecord.tenant_id == tenant_id,
                    DatasetReleaseReviewRecord.release_id == release_id,
                )
                .order_by(DatasetReleaseReviewRecord.role)
            )
        ).all()
        return tuple(
            DatasetReleaseReview(
                reviewer_id=item.reviewer_id,
                role=DatasetReviewRole(item.role),
                notes=item.notes,
                reviewed_at=item.reviewed_at,
            )
            for item in records
        )

    @staticmethod
    def _validate_audit(
        tenant_id: str,
        release_id: str,
        event: OutboxEvent,
    ) -> None:
        if (
            not isinstance(event, OutboxEvent)
            or event.tenant_id != tenant_id
            or event.aggregate_id != release_id
        ):
            raise AppValidationError("dataset release audit event does not match")
