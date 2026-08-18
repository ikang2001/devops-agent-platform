from dataclasses import dataclass
from typing import Any

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.dataset_release import DatasetReviewRole


@dataclass(frozen=True)
class CreateDatasetReleaseCommand:
    tenant_id: str
    release_id: str
    dataset_id: str
    source_version: str
    version: str
    dataset: dict[str, Any]
    candidate_sha256: str
    curation_review_sha256: str
    synthetic: bool
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("release_id", self.release_id, 64),
            ("dataset_id", self.dataset_id, 128),
            ("source_version", self.source_version, 32),
            ("version", self.version, 32),
            ("idempotency_key", self.idempotency_key, 128),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            _validate_text(field_name, value, maximum)
        for field_name, value in (
            ("candidate_sha256", self.candidate_sha256),
            ("curation_review_sha256", self.curation_review_sha256),
        ):
            _validate_sha256(field_name, value)
        if not isinstance(self.dataset, dict) or not isinstance(self.synthetic, bool):
            raise AppValidationError("dataset and synthetic are invalid")


@dataclass(frozen=True)
class ReviewDatasetReleaseCommand:
    tenant_id: str
    release_id: str
    role: DatasetReviewRole
    notes: str
    expected_revision: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("release_id", self.release_id, 64),
            ("notes", self.notes, 1024),
            ("idempotency_key", self.idempotency_key, 128),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            _validate_text(field_name, value, maximum)
        _validate_revision(self.expected_revision)


@dataclass(frozen=True)
class PublishDatasetReleaseCommand:
    tenant_id: str
    release_id: str
    expected_revision: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("release_id", self.release_id, 64),
            ("idempotency_key", self.idempotency_key, 128),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            _validate_text(field_name, value, maximum)
        _validate_revision(self.expected_revision)


def _validate_text(field_name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _validate_sha256(field_name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise AppValidationError(f"{field_name} must be a lowercase SHA-256")


def _validate_revision(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AppValidationError("expected_revision must be positive")
