from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class RemediationPlanRecord(Base):
    """人工审批、执行与回滚状态的持久化审计记录。"""

    __tablename__ = "remediation_plans"
    __table_args__ = (
        CheckConstraint(
            "risk IN ('LOW', 'MEDIUM', 'HIGH')",
            name="valid_remediation_risk",
        ),
        CheckConstraint(
            "status IN ("
            "'DRAFT','APPROVED','REJECTED','EXECUTING','SUCCEEDED','FAILED',"
            "'ROLLING_BACK','ROLLED_BACK','ROLLBACK_FAILED')",
            name="valid_remediation_status",
        ),
        CheckConstraint(
            "execution_attempt >= 0 AND rollback_attempt >= 0",
            name="non_negative_remediation_attempts",
        ),
        CheckConstraint(
            "("
            "(status = 'EXECUTING' "
            "AND execution_attempt >= 1 "
            "AND execution_started_at IS NOT NULL "
            "AND executed_by IS NOT NULL "
            "AND execution_lease_expires_at IS NOT NULL "
            "AND execution_lease_expires_at > execution_started_at) OR "
            "(status <> 'EXECUTING' AND execution_lease_expires_at IS NULL)"
            ")",
            name="valid_remediation_execution_lease",
        ),
        CheckConstraint(
            "("
            "(status = 'ROLLING_BACK' "
            "AND rollback_attempt >= 1 "
            "AND rollback_started_at IS NOT NULL "
            "AND rolled_back_by IS NOT NULL "
            "AND rollback_lease_expires_at IS NOT NULL "
            "AND rollback_lease_expires_at > rollback_started_at) OR "
            "(status <> 'ROLLING_BACK' AND rollback_lease_expires_at IS NULL)"
            ")",
            name="valid_remediation_rollback_lease",
        ),
        Index(
            "uq_remediation_tenant_create_idempotency",
            "tenant_id",
            "create_idempotency_key_hash",
            unique=True,
        ),
        Index(
            "ix_remediation_tenant_workflow_status",
            "tenant_id",
            "workflow_run_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_remediation_expired_execution_lease",
            "status",
            "execution_lease_expires_at",
            "remediation_plan_id",
        ),
        Index(
            "ix_remediation_expired_rollback_lease",
            "status",
            "rollback_lease_expires_at",
            "remediation_plan_id",
        ),
    )

    remediation_plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "workflow_runs.workflow_run_id",
            name="fk_remediation_plans_workflow_run_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    incident_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(String(256), nullable=False)
    expected_effect: Mapped[str] = mapped_column(Text, nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    rollback_action_key: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    dry_run_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    create_idempotency_key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    create_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    decision_trace_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    decision_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    execution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    executed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    execution_trace_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    execution_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    execution_attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    execution_lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    rollback_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    rolled_back_by: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    rollback_trace_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    rollback_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    rollback_attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    rollback_lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
