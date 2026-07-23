"""创建不可变 RCA 报告快照表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260630_0016"
down_revision: str | None = "20260630_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建报告表、业务唯一键和事故查询索引。"""
    op.create_table(
        "rca_reports",
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=64), nullable=False),
        sa.Column("execution_attempt", sa.Integer(), nullable=False),
        sa.Column(
            "conclusion_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("evidence_type_counts_json", sa.Text(), nullable=False),
        sa.Column("recommendations_json", sa.Text(), nullable=False),
        sa.Column("generator_name", sa.String(length=128), nullable=False),
        sa.Column("generator_version", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "conclusion_status IN "
            "('UNDETERMINED', 'CANDIDATE', 'CONFIRMED')",
            name=op.f("ck_rca_reports_valid_rca_conclusion_status"),
        ),
        sa.CheckConstraint(
            "execution_attempt >= 1",
            name=op.f("ck_rca_reports_positive_rca_report_attempt"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_rca_reports_valid_rca_report_confidence"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_rca_report_incident_id_incidents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.workflow_run_id"],
            name="fk_rca_report_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("report_id", name=op.f("pk_rca_reports")),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_run_id",
            "execution_attempt",
            name=op.f("uq_rca_report_workflow_attempt"),
        ),
    )
    op.create_index(
        "ix_rca_report_tenant_incident_generated",
        "rca_reports",
        ["tenant_id", "incident_id", "generated_at", "report_id"],
        unique=False,
    )


def downgrade() -> None:
    """删除 RCA 报告表及其索引。"""
    op.drop_index(
        "ix_rca_report_tenant_incident_generated",
        table_name="rca_reports",
    )
    op.drop_table("rca_reports")
