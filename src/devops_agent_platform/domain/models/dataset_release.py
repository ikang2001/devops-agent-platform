from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from devops_agent_platform.domain.exceptions import AppValidationError


class DatasetReleaseStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class DatasetReviewRole(StrEnum):
    DOMAIN = "domain"
    PRIVACY = "privacy"


@dataclass(frozen=True)
class DatasetReleaseReview:
    reviewer_id: str
    role: DatasetReviewRole
    notes: str
    reviewed_at: datetime

    def __post_init__(self) -> None:
        _text("reviewer_id", self.reviewer_id, 128, no_whitespace=True)
        _text("notes", self.notes, 1024)
        _aware("reviewed_at", self.reviewed_at)


@dataclass(frozen=True)
class DatasetRelease:
    release_id: str
    tenant_id: str
    dataset_id: str
    source_version: str
    version: str
    dataset_json: str
    dataset_sha256: str
    candidate_sha256: str
    curation_review_sha256: str
    requested_by: str
    synthetic: bool
    status: DatasetReleaseStatus
    revision: int
    reviews: tuple[DatasetReleaseReview, ...]
    created_at: datetime
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("release_id", self.release_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("dataset_id", self.dataset_id, 128),
            ("requested_by", self.requested_by, 128),
        ):
            _text(name, value, maximum, no_whitespace=True)
        _version("source_version", self.source_version)
        _version("version", self.version)
        if int(self.version[1:]) != int(self.source_version[1:]) + 1:
            raise AppValidationError(
                "dataset release version must follow source_version"
            )
        for name, value in (
            ("dataset_sha256", self.dataset_sha256),
            ("candidate_sha256", self.candidate_sha256),
            ("curation_review_sha256", self.curation_review_sha256),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise AppValidationError(f"{name} must be a lowercase SHA-256")
        try:
            dataset = json.loads(self.dataset_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AppValidationError("dataset_json is invalid") from exc
        if (
            not isinstance(dataset, dict)
            or len(self.dataset_json.encode()) > 1024 * 1024
        ):
            raise AppValidationError("dataset_json must be a bounded object")
        if not isinstance(self.synthetic, bool):
            raise AppValidationError("synthetic must be a boolean")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise AppValidationError("dataset release revision must be positive")
        _aware("created_at", self.created_at)
        if self.published_at is not None:
            _aware("published_at", self.published_at)
        if len({item.reviewer_id for item in self.reviews}) != len(self.reviews):
            raise AppValidationError("dataset release reviewers must be distinct")
        if len({item.role for item in self.reviews}) != len(self.reviews):
            raise AppValidationError("dataset release review roles must be distinct")
        if any(item.reviewer_id == self.requested_by for item in self.reviews):
            raise AppValidationError("dataset release requester cannot self-review")
        if self.status is DatasetReleaseStatus.PUBLISHED:
            if {item.role for item in self.reviews} != {
                DatasetReviewRole.DOMAIN,
                DatasetReviewRole.PRIVACY,
            } or self.published_at is None:
                raise AppValidationError(
                    "published dataset release lacks review quorum"
                )
        elif self.published_at is not None:
            raise AppValidationError("draft dataset release cannot have published_at")


def _text(name: str, value: str, maximum: int, *, no_whitespace: bool = False) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or (no_whitespace and any(character.isspace() for character in value))
    ):
        raise AppValidationError(f"{name} is invalid")


def _version(name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) < 2
        or value[0] != "v"
        or not value[1:].isdigit()
        or value[1] == "0"
    ):
        raise AppValidationError(f"{name} is invalid")


def _aware(name: str, value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise AppValidationError(f"{name} must include timezone information")
