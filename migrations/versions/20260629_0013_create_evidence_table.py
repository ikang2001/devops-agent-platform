"""创建 RCA 证据表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260629_0013"
down_revision: str | None = "20260629_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建保存有限结构化 RCA 证据的表和查询索引。"""
    op.create_table(
        "evidence",
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=64), nullable=False),
        sa.Column("execution_attempt", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("tool_version", sa.String(length=64), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evidence_type IN "
            "('METRIC', 'LOG', 'TRACE', 'DEPLOYMENT', "
            "'RUNBOOK', 'INCIDENT_HISTORY')",
            name=op.f("ck_evidence_valid_evidence_type"),
        ),
        sa.CheckConstraint(
            "execution_attempt >= 1",
            name=op.f("ck_evidence_positive_execution_attempt"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_evidence_valid_confidence"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_evidence_incident_id_incidents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.workflow_run_id"],
            name="fk_evidence_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "evidence_id",
            name=op.f("pk_evidence"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_run_id",
            "execution_attempt",
            "step_id",
            "evidence_type",
            "source",
            name=op.f("uq_evidence_workflow_step_source"),
        ),
    )
    op.create_index(
        "ix_evidence_tenant_incident_collected",
        "evidence",
        ["tenant_id", "incident_id", "collected_at", "evidence_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_tenant_workflow_step",
        "evidence",
        ["tenant_id", "workflow_run_id", "execution_attempt", "step_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_tenant_type_collected",
        "evidence",
        ["tenant_id", "evidence_type", "collected_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除 RCA 证据表及其索引。"""
    op.drop_index(
        "ix_evidence_tenant_type_collected",
        table_name="evidence",
    )
    op.drop_index(
        "ix_evidence_tenant_workflow_step",
        table_name="evidence",
    )
    op.drop_index(
        "ix_evidence_tenant_incident_collected",
        table_name="evidence",
    )
    op.drop_table("evidence")
