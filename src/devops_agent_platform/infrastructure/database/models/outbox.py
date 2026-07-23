from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime

PayloadType = JSON().with_variant(JSONB(), "postgresql")


class OutboxEventRecord(Base):
    """等待异步发布的事务型 Outbox 数据库记录。"""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED')",
            name="valid_status",
        ),
        CheckConstraint("schema_version >= 1", name="positive_schema_version"),
        CheckConstraint("attempts >= 0", name="non_negative_attempts"),
        Index(
            "ix_outbox_dispatch",
            "status",
            "available_at",
            "created_at",
            "event_id",
        ),
        Index(
            "ix_outbox_aggregate",
            "tenant_id",
            "aggregate_type",
            "aggregate_id",
            "created_at",
        ),
        Index(
            "ix_outbox_recovery",
            "status",
            "locked_until",
            "event_id",
        ),
        Index(
            "ix_outbox_backlog_status_created",
            "status",
            "created_at",
            postgresql_where=text(
                "status IN ('PENDING', 'PROCESSING', 'FAILED')"
            ),
            sqlite_where=text(
                "status IN ('PENDING', 'PROCESSING', 'FAILED')"
            ),
        ),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(PayloadType, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    available_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    lock_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
