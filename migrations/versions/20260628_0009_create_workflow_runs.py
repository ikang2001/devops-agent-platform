"""创建RCA工作流运行记录表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_0009"
down_revision: str | None = "20260628_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建状态约束、幂等唯一键和活跃运行部分唯一索引。"""
    op.create_table(
        "workflow_runs",
        sa.Column("workflow_run_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("operator_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "step_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED')",
            name=op.f("ck_workflow_runs_valid_status"),
        ),
        sa.CheckConstraint(
            "step_count >= 0",
            name=op.f("ck_workflow_runs_non_negative_step_count"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_workflow_runs_positive_version"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_workflow_runs_valid_updated_at"),
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name=op.f("ck_workflow_runs_valid_started_at"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= COALESCE(started_at, created_at)",
            name=op.f("ck_workflow_runs_valid_ended_at"),
        ),
        sa.CheckConstraint(
            "("
            "(status = 'PENDING' AND started_at IS NULL AND ended_at IS NULL) OR "
            "(status = 'RUNNING' AND started_at IS NOT NULL AND ended_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'FAILED') "
            "AND started_at IS NOT NULL AND ended_at IS NOT NULL) OR "
            "(status = 'CANCELED' AND ended_at IS NOT NULL)"
            ")",
            name=op.f("ck_workflow_runs_valid_status_timeline"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name=op.f("fk_workflow_runs_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "workflow_run_id",
            name=op.f("pk_workflow_runs"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key_hash",
            name="uq_workflow_runs_tenant_idempotency",
        ),
    )
    op.create_index(
        "uq_workflow_runs_active_incident",
        "workflow_runs",
        ["tenant_id", "incident_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    op.create_index(
        "ix_workflow_runs_tenant_incident_created",
        "workflow_runs",
        ["tenant_id", "incident_id", "created_at", "workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_tenant_status_updated",
        "workflow_runs",
        ["tenant_id", "status", "updated_at", "workflow_run_id"],
        unique=False,
    )


def downgrade() -> None:
    """删除WorkflowRun索引和表。"""
    op.drop_index(
        "ix_workflow_runs_tenant_status_updated",
        table_name="workflow_runs",
    )
    op.drop_index(
        "ix_workflow_runs_tenant_incident_created",
        table_name="workflow_runs",
    )
    op.drop_index(
        "uq_workflow_runs_active_incident",
        table_name="workflow_runs",
    )
    op.drop_table("workflow_runs")
