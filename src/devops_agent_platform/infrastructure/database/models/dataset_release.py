from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class DatasetReleaseRecord(Base):
    __tablename__ = "dataset_releases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "version",
            name="uq_dataset_release_version",
        ),
        Index("ix_dataset_releases_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_json: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    curation_review_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    create_idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    create_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    publish_idempotency_hash: Mapped[str | None] = mapped_column(String(64))
    publish_request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DatasetReleaseReviewRecord(Base):
    __tablename__ = "dataset_release_reviews"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "release_id",
            "role",
            name="uq_dataset_release_review_role",
        ),
        UniqueConstraint(
            "tenant_id",
            "release_id",
            "idempotency_key_hash",
            name="uq_dataset_release_review_idempotency",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reviewer_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
