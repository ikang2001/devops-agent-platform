from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class WorkflowRunRecord(Base):
    """RCA工作流运行记录，包含幂等和并发控制约束。"""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED')",
            name="valid_status",
        ),
        CheckConstraint("step_count >= 0", name="non_negative_step_count"),
        CheckConstraint(
            "execution_attempts >= 0",
            name="non_negative_execution_attempts",
        ),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint(
            "updated_at >= created_at",
            name="valid_updated_at",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="valid_started_at",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= COALESCE(started_at, created_at)",
            name="valid_ended_at",
        ),
        CheckConstraint(
            "audit_purged_at IS NULL OR "
            "(ended_at IS NOT NULL AND audit_purged_at >= ended_at)",
            name="valid_audit_purged_at",
        ),
        CheckConstraint(
            "(canceled_by IS NULL AND cancellation_reason IS NULL "
            "AND canceled_at IS NULL "
            "AND cancellation_idempotency_key_hash IS NULL "
            "AND cancellation_request_hash IS NULL "
            "AND cancellation_trace_id IS NULL) OR "
            "(status = 'CANCELED' "
            "AND canceled_by IS NOT NULL "
            "AND cancellation_reason IS NOT NULL "
            "AND canceled_at IS NOT NULL "
            "AND cancellation_idempotency_key_hash IS NOT NULL "
            "AND cancellation_request_hash IS NOT NULL "
            "AND cancellation_trace_id IS NOT NULL "
            "AND canceled_at = ended_at)",
            name="valid_cancellation_state",
        ),
        CheckConstraint(
            "("
            "(status = 'PENDING' AND started_at IS NULL AND ended_at IS NULL) OR "
            "(status = 'RUNNING' AND started_at IS NOT NULL AND ended_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'FAILED') "
            "AND started_at IS NOT NULL AND ended_at IS NOT NULL) OR "
            "(status = 'CANCELED' AND ended_at IS NOT NULL)"
            ")",
            name="valid_status_timeline",
        ),
        CheckConstraint(
            "("
            "(status = 'RUNNING' "
            "AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL "
            "AND heartbeat_at >= started_at "
            "AND lease_expires_at > heartbeat_at "
            "AND execution_attempts >= 1) OR "
            "(status <> 'RUNNING' "
            "AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL "
            "AND heartbeat_at IS NULL)"
            ")",
            name="valid_execution_lease",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key_hash",
            name="uq_workflow_runs_tenant_idempotency",
        ),
        Index(
            "uq_workflow_runs_tenant_cancellation_idempotency",
            "tenant_id",
            "cancellation_idempotency_key_hash",
            unique=True,
        ),
        Index(
            "uq_workflow_runs_active_incident",
            "tenant_id",
            "incident_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
        Index(
            "ix_workflow_runs_tenant_incident_created",
            "tenant_id",
            "incident_id",
            "created_at",
            "workflow_run_id",
        ),
        Index(
            "ix_workflow_runs_tenant_status_updated",
            "tenant_id",
            "status",
            "updated_at",
            "workflow_run_id",
        ),
        Index(
            "ix_workflow_runs_expired_lease",
            "status",
            "lease_expires_at",
            "workflow_run_id",
            postgresql_where=text("status = 'RUNNING'"),
            sqlite_where=text("status = 'RUNNING'"),
        ),
        Index(
            "ix_workflow_runs_audit_retention",
            "ended_at",
            "workflow_run_id",
            postgresql_where=text(
                "audit_purged_at IS NULL "
                "AND status IN ('SUCCEEDED', 'FAILED', 'CANCELED')"
            ),
            sqlite_where=text(
                "audit_purged_at IS NULL "
                "AND status IN ('SUCCEEDED', 'FAILED', 'CANCELED')"
            ),
        ),
    )

    workflow_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    incident_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("incidents.incident_id", ondelete="RESTRICT"),
        nullable=False,
    )
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    audit_purged_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    canceled_by: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    cancellation_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    cancellation_request_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    cancellation_trace_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    step_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    lease_owner: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    execution_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    __mapper_args__ = {"version_id_col": version}
