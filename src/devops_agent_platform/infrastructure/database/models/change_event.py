from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.domain.enums import ChangeEventStatus, ChangeType
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class ChangeEventRecord(Base):
    """变更事件事实的数据库记录。"""

    __tablename__ = "change_events"
    __table_args__ = (
        CheckConstraint(
            f"change_type IN ({', '.join(repr(item.value) for item in ChangeType)})",
            name="valid_change_type",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(item.value) for item in ChangeEventStatus)})",
            name="valid_change_event_status",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="valid_completion_time",
        ),
        UniqueConstraint(
            "tenant_id",
            "source",
            "external_event_id",
            name="uq_change_events_tenant_source_external_event",
        ),
        Index(
            "ix_change_events_tenant_service_started",
            "tenant_id",
            "service_name",
            "started_at",
            "change_event_id",
        ),
    )

    change_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    service_name: Mapped[str] = mapped_column(String(256), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version_before: Mapped[str | None] = mapped_column(String(256), nullable=True)
    version_after: Mapped[str | None] = mapped_column(String(256), nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
