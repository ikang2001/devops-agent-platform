from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from devops_agent_platform.domain.exceptions import AppValidationError


class KnowledgeDocumentType(StrEnum):
    HISTORICAL_INCIDENT = "HISTORICAL_INCIDENT"
    POSTMORTEM = "POSTMORTEM"
    RUNBOOK = "RUNBOOK"
    KNOWN_ERROR = "KNOWN_ERROR"
    RESOLUTION_NOTE = "RESOLUTION_NOTE"


class KnowledgeReviewStatus(StrEnum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


def _text(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")


def _time(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise AppValidationError(f"{name} must include timezone information")


@dataclass(frozen=True)
class KnowledgeDocument:
    document_id: str
    tenant_id: str
    document_type: KnowledgeDocumentType
    title: str
    service_name: str
    body: str
    source_incident_id: str | None = None
    error_fingerprint: str | None = None
    version: int = 1
    review_status: KnowledgeReviewStatus = KnowledgeReviewStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    published_at: datetime | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("document_id", self.document_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("title", self.title, 256),
            ("service_name", self.service_name, 256),
            ("body", self.body, 64 * 1024),
        ):
            _text(name, value, maximum)
        if not isinstance(self.document_type, KnowledgeDocumentType) or not isinstance(
            self.review_status, KnowledgeReviewStatus
        ):
            raise AppValidationError("knowledge type/status is invalid")
        if self.source_incident_id is not None:
            _text("source_incident_id", self.source_incident_id, 64)
        if self.error_fingerprint is not None:
            _text("error_fingerprint", self.error_fingerprint, 256)
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < 1
        ):
            raise AppValidationError("version must be a positive integer")
        _time("created_at", self.created_at)
        _time("published_at", self.published_at)
        if (
            self.review_status
            in {KnowledgeReviewStatus.PUBLISHED, KnowledgeReviewStatus.APPROVED}
            and self.published_at is None
        ):
            raise AppValidationError("published knowledge must have published_at")
        if (
            self.review_status
            not in {KnowledgeReviewStatus.PUBLISHED, KnowledgeReviewStatus.APPROVED}
            and self.published_at is not None
        ):
            raise AppValidationError("unpublished knowledge cannot have published_at")

    @property
    def searchable_text(self) -> str:
        return " ".join(
            (self.title, self.service_name, self.body, self.error_fingerprint or "")
        ).casefold()

    def publish(self, now: datetime) -> KnowledgeDocument:
        _time("now", now)
        return KnowledgeDocument(
            **{
                **self.__dict__,
                "review_status": KnowledgeReviewStatus.PUBLISHED,
                "published_at": now,
            }
        )


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    document_id: str
    tenant_id: str
    ordinal: int
    content: str
    token_count: int

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("chunk_id", self.chunk_id, 64),
            ("document_id", self.document_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("content", self.content, 16 * 1024),
        ):
            _text(name, value, maximum)
        if (
            isinstance(self.ordinal, bool)
            or not isinstance(self.ordinal, int)
            or self.ordinal < 0
        ):
            raise AppValidationError("ordinal must be non-negative")
        if (
            isinstance(self.token_count, bool)
            or not isinstance(self.token_count, int)
            or not 1 <= self.token_count <= 8192
        ):
            raise AppValidationError("token_count must be between 1 and 8192")
