"""创建工具调用审计表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260630_0014"
down_revision: str | None = "20260629_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建只保存有限摘要和哈希的工具调用审计表。"""
    op.create_table(
        "tool_invocations",
        sa.Column("invocation_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=64), nullable=False),
        sa.Column("execution_attempt", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(length=128), nullable=False),
        sa.Column("operator_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("tool_version", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_summary", sa.String(length=256), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED')",
            name=op.f("ck_tool_invocations_valid_tool_invocation_terminal_status"),
        ),
        sa.CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH')",
            name=op.f("ck_tool_invocations_valid_tool_invocation_risk_level"),
        ),
        sa.CheckConstraint(
            "execution_attempt >= 1",
            name=op.f("ck_tool_invocations_positive_tool_invocation_attempt"),
        ),
        sa.CheckConstraint(
            "latency_ms >= 0",
            name=op.f("ck_tool_invocations_non_negative_tool_invocation_latency"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_tool_invocation_incident_id_incidents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.workflow_run_id"],
            name="fk_tool_invocation_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "invocation_id",
            name=op.f("pk_tool_invocations"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_run_id",
            "execution_attempt",
            "step_id",
            name=op.f("uq_tool_invocation_workflow_step"),
        ),
    )
    op.create_index(
        "ix_tool_invocation_tenant_workflow_started",
        "tool_invocations",
        ["tenant_id", "workflow_run_id", "started_at", "invocation_id"],
        unique=False,
    )
    op.create_index(
        "ix_tool_invocation_tenant_incident_started",
        "tool_invocations",
        ["tenant_id", "incident_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_tool_invocation_tenant_status_started",
        "tool_invocations",
        ["tenant_id", "status", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除工具调用审计表及其索引。"""
    op.drop_index(
        "ix_tool_invocation_tenant_status_started",
        table_name="tool_invocations",
    )
    op.drop_index(
        "ix_tool_invocation_tenant_incident_started",
        table_name="tool_invocations",
    )
    op.drop_index(
        "ix_tool_invocation_tenant_workflow_started",
        table_name="tool_invocations",
    )
    op.drop_table("tool_invocations")
