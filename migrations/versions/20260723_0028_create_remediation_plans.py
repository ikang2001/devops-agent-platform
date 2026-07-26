"""创建带执行/回滚租约的受控自动修复计划表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0028"
down_revision: str | None = "20260723_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "remediation_plans",
        sa.Column("remediation_plan_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workflow_run_id", sa.String(64), nullable=False),
        sa.Column("incident_id", sa.String(64), nullable=False),
        sa.Column("action_key", sa.String(128), nullable=False),
        sa.Column("target", sa.String(256), nullable=False),
        sa.Column("expected_effect", sa.Text(), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("rollback_action_key", sa.String(128), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("dry_run_summary", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("create_idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("create_request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_trace_id", sa.String(128), nullable=True),
        sa.Column("decision_idempotency_key_hash", sa.String(64), nullable=True),
        sa.Column("execution_summary", sa.Text(), nullable=True),
        sa.Column(
            "execution_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_by", sa.String(128), nullable=True),
        sa.Column("execution_trace_id", sa.String(128), nullable=True),
        sa.Column("execution_idempotency_key_hash", sa.String(64), nullable=True),
        sa.Column(
            "execution_attempt",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "execution_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("rollback_summary", sa.Text(), nullable=True),
        sa.Column(
            "rollback_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_by", sa.String(128), nullable=True),
        sa.Column("rollback_trace_id", sa.String(128), nullable=True),
        sa.Column("rollback_idempotency_key_hash", sa.String(64), nullable=True),
        sa.Column(
            "rollback_attempt",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "rollback_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "risk IN ('LOW', 'MEDIUM', 'HIGH')",
            name=op.f("ck_remediation_plans_valid_remediation_risk"),
        ),
        sa.CheckConstraint(
            "status IN ("
            "'DRAFT','APPROVED','REJECTED','EXECUTING','SUCCEEDED','FAILED',"
            "'ROLLING_BACK','ROLLED_BACK','ROLLBACK_FAILED')",
            name=op.f("ck_remediation_plans_valid_remediation_status"),
        ),
        sa.CheckConstraint(
            "execution_attempt >= 0 AND rollback_attempt >= 0",
            name=op.f("ck_remediation_plans_non_negative_attempts"),
        ),
        sa.CheckConstraint(
            "("
            "(status = 'EXECUTING' "
            "AND execution_attempt >= 1 "
            "AND execution_started_at IS NOT NULL "
            "AND executed_by IS NOT NULL "
            "AND execution_lease_expires_at IS NOT NULL "
            "AND execution_lease_expires_at > execution_started_at) OR "
            "(status <> 'EXECUTING' AND execution_lease_expires_at IS NULL)"
            ")",
            name=op.f("ck_remediation_plans_valid_execution_lease"),
        ),
        sa.CheckConstraint(
            "("
            "(status = 'ROLLING_BACK' "
            "AND rollback_attempt >= 1 "
            "AND rollback_started_at IS NOT NULL "
            "AND rolled_back_by IS NOT NULL "
            "AND rollback_lease_expires_at IS NOT NULL "
            "AND rollback_lease_expires_at > rollback_started_at) OR "
            "(status <> 'ROLLING_BACK' AND rollback_lease_expires_at IS NULL)"
            ")",
            name=op.f("ck_remediation_plans_valid_rollback_lease"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.workflow_run_id"],
            name=op.f("fk_remediation_plans_workflow_run_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "remediation_plan_id",
            name=op.f("pk_remediation_plans"),
        ),
    )
    op.create_index(
        "uq_remediation_tenant_create_idempotency",
        "remediation_plans",
        ["tenant_id", "create_idempotency_key_hash"],
        unique=True,
    )
    op.create_index(
        "ix_remediation_tenant_workflow_status",
        "remediation_plans",
        ["tenant_id", "workflow_run_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_remediation_expired_execution_lease",
        "remediation_plans",
        ["status", "execution_lease_expires_at", "remediation_plan_id"],
        unique=False,
        postgresql_where=sa.text("status = 'EXECUTING'"),
    )
    op.create_index(
        "ix_remediation_expired_rollback_lease",
        "remediation_plans",
        ["status", "rollback_lease_expires_at", "remediation_plan_id"],
        unique=False,
        postgresql_where=sa.text("status = 'ROLLING_BACK'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_remediation_expired_rollback_lease",
        table_name="remediation_plans",
    )
    op.drop_index(
        "ix_remediation_expired_execution_lease",
        table_name="remediation_plans",
    )
    op.drop_index(
        "ix_remediation_tenant_workflow_status",
        table_name="remediation_plans",
    )
    op.drop_index(
        "uq_remediation_tenant_create_idempotency",
        table_name="remediation_plans",
    )
    op.drop_table("remediation_plans")
