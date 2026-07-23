from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class IncidentRecord(Base):
    """可变事故聚合的数据库记录。

    ``version`` 由 SQLAlchemy 在更新时递增，用于检测两个并发请求基于同一旧版本
    覆盖彼此状态的情况。
    """

    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'CRITICAL')",
            name="valid_severity",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'ANALYZING', 'RESOLVED', 'CLOSED')",
            name="valid_status",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="valid_time_range",
        ),
        CheckConstraint(
            "(resolved_by IS NULL AND resolution_reason IS NULL "
            "AND resolved_at IS NULL "
            "AND resolution_idempotency_key_hash IS NULL "
            "AND resolution_request_hash IS NULL "
            "AND resolution_trace_id IS NULL) OR "
            "(status IN ('RESOLVED', 'CLOSED') "
            "AND resolved_by IS NOT NULL "
            "AND resolution_reason IS NOT NULL "
            "AND resolved_at IS NOT NULL "
            "AND resolution_idempotency_key_hash IS NOT NULL "
            "AND resolution_request_hash IS NOT NULL "
            "AND resolution_trace_id IS NOT NULL)",
            name="valid_incident_resolution_state",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR "
            "(resolved_at >= created_at AND resolved_at <= updated_at)",
            name="valid_incident_resolution_time",
        ),
        CheckConstraint(
            "(closed_by IS NULL AND closure_reason IS NULL "
            "AND closed_at IS NULL "
            "AND closure_idempotency_key_hash IS NULL "
            "AND closure_request_hash IS NULL "
            "AND closure_trace_id IS NULL) OR "
            "(status = 'CLOSED' "
            "AND closed_by IS NOT NULL "
            "AND closure_reason IS NOT NULL "
            "AND closed_at IS NOT NULL "
            "AND closure_idempotency_key_hash IS NOT NULL "
            "AND closure_request_hash IS NOT NULL "
            "AND closure_trace_id IS NOT NULL)",
            name="valid_incident_closure_state",
        ),
        CheckConstraint(
            "closed_at IS NULL OR "
            "(closed_at >= created_at AND closed_at <= updated_at)",
            name="valid_incident_closure_time",
        ),
        CheckConstraint("version >= 1", name="positive_version"),
        Index(
            "uq_incidents_tenant_resolution_idempotency",
            "tenant_id",
            "resolution_idempotency_key_hash",
            unique=True,
        ),
        Index(
            "uq_incidents_tenant_closure_idempotency",
            "tenant_id",
            "closure_idempotency_key_hash",
            unique=True,
        ),
        Index(
            "ix_incidents_tenant_status_updated_id",
            "tenant_id",
            "status",
            "updated_at",
            "incident_id",
        ),
        Index(
            "ix_incidents_tenant_service_created_id",
            "tenant_id",
            "service_name",
            "created_at",
            "incident_id",
        ),
        Index(
            "ix_incidents_candidate_lookup",
            "tenant_id",
            "service_name",
            "updated_at",
            "incident_id",
        ),
    )
    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    service_name: Mapped[str] = mapped_column(String(256), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    resolution_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    resolution_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    resolution_request_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    resolution_trace_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    closed_by: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    closure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    closure_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    closure_request_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    closure_trace_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    __mapper_args__ = {"version_id_col": version}
