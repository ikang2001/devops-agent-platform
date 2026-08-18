from __future__ import annotations

from typing import Protocol

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.models.dataset_release import (
    DatasetRelease,
    DatasetReleaseReview,
)


class DatasetReleaseStorePort(Protocol):
    async def create(
        self,
        release: DatasetRelease,
        *,
        idempotency_key_hash: str,
        request_hash: str,
        audit_event: OutboxEvent,
    ) -> tuple[DatasetRelease, bool]: ...

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
    ) -> tuple[DatasetRelease, bool]: ...

    async def publish(
        self,
        tenant_id: str,
        release_id: str,
        *,
        expected_revision: int,
        published_at,
        idempotency_key_hash: str,
        request_hash: str,
        audit_event: OutboxEvent,
    ) -> tuple[DatasetRelease, bool]: ...

    async def get(self, tenant_id: str, release_id: str) -> DatasetRelease | None: ...
