from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from devops_agent_platform.application.commands.dataset_releases import (
    CreateDatasetReleaseCommand,
    PublishDatasetReleaseCommand,
    ReviewDatasetReleaseCommand,
)
from devops_agent_platform.application.services.dataset_release_service import (
    DatasetReleaseService,
)
from devops_agent_platform.domain.exceptions import ConflictError, PermissionDenied
from devops_agent_platform.domain.models.dataset_release import (
    DatasetReleaseStatus,
    DatasetReviewRole,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyDatasetReleaseStore,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)
from devops_agent_platform.infrastructure.identifiers import UUIDIdentifierGenerator


@pytest.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'releases.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def create_command() -> CreateDatasetReleaseCommand:
    return CreateDatasetReleaseCommand(
        tenant_id="tenant-a",
        release_id="release-v2",
        dataset_id="rca-benchmark",
        source_version="v1",
        version="v2",
        dataset={
            "dataset_id": "rca-benchmark",
            "version": "v2",
            "privacy": "anonymized",
            "samples": [{"case_id": "checkout-timeout-001"}],
        },
        candidate_sha256="a" * 64,
        curation_review_sha256="b" * 64,
        synthetic=True,
        idempotency_key="create-release-v2",
        requested_by="release-owner",
        trace_id="trace-release-create",
    )


def review_command(
    *,
    reviewer: str,
    role: DatasetReviewRole,
    revision: int,
) -> ReviewDatasetReleaseCommand:
    return ReviewDatasetReleaseCommand(
        tenant_id="tenant-a",
        release_id="release-v2",
        role=role,
        notes=f"{role.value} review completed.",
        expected_revision=revision,
        idempotency_key=f"review-{role.value}",
        requested_by=reviewer,
        trace_id=f"trace-review-{role.value}",
    )


@pytest.mark.asyncio
async def test_online_release_requires_separated_quorum_and_is_immutable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = DatasetReleaseService(
        SQLAlchemyDatasetReleaseStore(session_factory),
        UUIDIdentifierGenerator(),
    )

    created = await service.create(create_command())
    duplicate_create = await service.create(create_command())
    assert created.release.revision == 1
    assert duplicate_create.is_duplicate is True

    with pytest.raises(PermissionDenied, match="self-review"):
        await service.review(
            review_command(
                reviewer="release-owner",
                role=DatasetReviewRole.DOMAIN,
                revision=1,
            )
        )

    domain = await service.review(
        review_command(
            reviewer="domain-reviewer",
            role=DatasetReviewRole.DOMAIN,
            revision=1,
        )
    )
    privacy = await service.review(
        review_command(
            reviewer="privacy-reviewer",
            role=DatasetReviewRole.PRIVACY,
            revision=2,
        )
    )
    assert domain.release.revision == 2
    assert privacy.release.revision == 3

    publish = PublishDatasetReleaseCommand(
        tenant_id="tenant-a",
        release_id="release-v2",
        expected_revision=3,
        idempotency_key="publish-release-v2",
        requested_by="release-publisher",
        trace_id="trace-release-publish",
    )
    published = await service.publish(publish)
    duplicate_publish = await service.publish(publish)

    assert published.release.status is DatasetReleaseStatus.PUBLISHED
    assert published.release.revision == 4
    assert duplicate_publish.is_duplicate is True
    assert len(published.release.reviews) == 2
    with pytest.raises(ConflictError, match="immutable"):
        await service.review(
            replace(
                review_command(
                    reviewer="another-reviewer",
                    role=DatasetReviewRole.DOMAIN,
                    revision=4,
                ),
                idempotency_key="review-after-publish",
            )
        )
    async with session_factory() as session:
        event_count = await session.scalar(
            select(func.count()).select_from(OutboxEventRecord)
        )
    assert event_count == 4
