from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from devops_agent_platform.application.commands.dataset_releases import (
    CreateDatasetReleaseCommand,
    PublishDatasetReleaseCommand,
    ReviewDatasetReleaseCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import AppValidationError, ResourceNotFound
from devops_agent_platform.domain.models.dataset_release import (
    DatasetRelease,
    DatasetReleaseReview,
    DatasetReleaseStatus,
)
from devops_agent_platform.ports.dataset_release import DatasetReleaseStorePort
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class DatasetReleaseResult:
    release: DatasetRelease
    trace_id: str
    is_duplicate: bool

    def to_dict(self, *, include_dataset: bool = True) -> dict[str, Any]:
        release = self.release
        value = {
            "release_id": release.release_id,
            "tenant_id": release.tenant_id,
            "dataset_id": release.dataset_id,
            "source_version": release.source_version,
            "version": release.version,
            "dataset_sha256": release.dataset_sha256,
            "candidate_sha256": release.candidate_sha256,
            "curation_review_sha256": release.curation_review_sha256,
            "synthetic": release.synthetic,
            "status": release.status.value,
            "revision": release.revision,
            "requested_by": release.requested_by,
            "created_at": release.created_at.isoformat(),
            "published_at": (
                release.published_at.isoformat()
                if release.published_at is not None
                else None
            ),
            "reviews": [
                {
                    **asdict(review),
                    "role": review.role.value,
                    "reviewed_at": review.reviewed_at.isoformat(),
                }
                for review in release.reviews
            ],
            "trace_id": self.trace_id,
            "is_duplicate": self.is_duplicate,
        }
        if include_dataset:
            value["dataset"] = json.loads(release.dataset_json)
        return value


class DatasetReleaseService:
    def __init__(
        self,
        store: DatasetReleaseStorePort,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._identifiers = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create(
        self,
        command: CreateDatasetReleaseCommand,
    ) -> DatasetReleaseResult:
        dataset_json = _validate_dataset(command)
        now = self._now()
        release = DatasetRelease(
            release_id=command.release_id,
            tenant_id=command.tenant_id,
            dataset_id=command.dataset_id,
            source_version=command.source_version,
            version=command.version,
            dataset_json=dataset_json,
            dataset_sha256=sha256(dataset_json.encode()).hexdigest(),
            candidate_sha256=command.candidate_sha256,
            curation_review_sha256=command.curation_review_sha256,
            requested_by=command.requested_by,
            synthetic=command.synthetic,
            status=DatasetReleaseStatus.DRAFT,
            revision=1,
            reviews=(),
            created_at=now,
        )
        request_hash = _hash(
            {
                "tenant_id": command.tenant_id,
                "release_id": command.release_id,
                "dataset_sha256": release.dataset_sha256,
                "candidate_sha256": command.candidate_sha256,
                "curation_review_sha256": command.curation_review_sha256,
                "requested_by": command.requested_by,
            }
        )
        stored, duplicate = await self._store.create(
            release,
            idempotency_key_hash=_hash_text(command.idempotency_key),
            request_hash=request_hash,
            audit_event=self._event(
                release,
                "dataset.release.created",
                command.trace_id,
                now,
                {"dataset_sha256": release.dataset_sha256},
            ),
        )
        return DatasetReleaseResult(stored, command.trace_id, duplicate)

    async def review(
        self,
        command: ReviewDatasetReleaseCommand,
    ) -> DatasetReleaseResult:
        now = self._now()
        review = DatasetReleaseReview(
            reviewer_id=command.requested_by,
            role=command.role,
            notes=redact_sensitive_text(command.notes)[0],
            reviewed_at=now,
        )
        request_hash = _hash(
            {
                "tenant_id": command.tenant_id,
                "release_id": command.release_id,
                "role": command.role.value,
                "notes": review.notes,
                "expected_revision": command.expected_revision,
                "requested_by": command.requested_by,
            }
        )
        audit = self._event_for_identity(
            tenant_id=command.tenant_id,
            release_id=command.release_id,
            event_type="dataset.release.reviewed",
            trace_id=command.trace_id,
            occurred_at=now,
            payload={"reviewer_id": command.requested_by, "role": command.role.value},
        )
        stored, duplicate = await self._store.add_review(
            command.tenant_id,
            command.release_id,
            review,
            expected_revision=command.expected_revision,
            idempotency_key_hash=_hash_text(command.idempotency_key),
            request_hash=request_hash,
            audit_event=audit,
        )
        return DatasetReleaseResult(stored, command.trace_id, duplicate)

    async def publish(
        self,
        command: PublishDatasetReleaseCommand,
    ) -> DatasetReleaseResult:
        now = self._now()
        request_hash = _hash(
            {
                "tenant_id": command.tenant_id,
                "release_id": command.release_id,
                "expected_revision": command.expected_revision,
                "requested_by": command.requested_by,
            }
        )
        audit = self._event_for_identity(
            tenant_id=command.tenant_id,
            release_id=command.release_id,
            event_type="dataset.release.published",
            trace_id=command.trace_id,
            occurred_at=now,
            payload={"published_by": command.requested_by},
        )
        stored, duplicate = await self._store.publish(
            command.tenant_id,
            command.release_id,
            expected_revision=command.expected_revision,
            published_at=now,
            idempotency_key_hash=_hash_text(command.idempotency_key),
            request_hash=request_hash,
            audit_event=audit,
        )
        return DatasetReleaseResult(stored, command.trace_id, duplicate)

    async def get(
        self,
        tenant_id: str,
        release_id: str,
        trace_id: str,
    ) -> DatasetReleaseResult:
        release = await self._store.get(tenant_id, release_id)
        if release is None:
            raise ResourceNotFound("dataset release not found")
        return DatasetReleaseResult(release, trace_id, False)

    def _event(
        self,
        release: DatasetRelease,
        event_type: str,
        trace_id: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> OutboxEvent:
        return self._event_for_identity(
            tenant_id=release.tenant_id,
            release_id=release.release_id,
            event_type=event_type,
            trace_id=trace_id,
            occurred_at=occurred_at,
            payload=payload,
        )

    def _event_for_identity(
        self,
        *,
        tenant_id: str,
        release_id: str,
        event_type: str,
        trace_id: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> OutboxEvent:
        return OutboxEvent(
            event_id=self._identifiers.new_event_id(),
            tenant_id=tenant_id,
            aggregate_type="DatasetRelease",
            aggregate_id=release_id,
            event_type=event_type,
            schema_version=1,
            payload={"release_id": release_id, **payload},
            occurred_at=occurred_at,
            trace_id=trace_id,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _validate_dataset(command: CreateDatasetReleaseCommand) -> str:
    dataset = command.dataset
    if (
        not isinstance(dataset, dict)
        or dataset.get("dataset_id") != command.dataset_id
        or dataset.get("version") != command.version
        or dataset.get("privacy") != "anonymized"
        or not isinstance(dataset.get("samples"), list)
        or not dataset["samples"]
    ):
        raise AppValidationError("published dataset metadata is invalid")
    serialized = json.dumps(
        dataset,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(serialized.encode()) > 1024 * 1024:
        raise AppValidationError("published dataset exceeds size limit")
    for value in _walk_strings(dataset):
        _, changed = redact_sensitive_text(value)
        if changed or "[redacted" in value.casefold():
            raise AppValidationError("published dataset contains sensitive text")
    return serialized


def _walk_strings(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str):
        yield value


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _hash_text(value: str) -> str:
    return sha256(value.encode()).hexdigest()
