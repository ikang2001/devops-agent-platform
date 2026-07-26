"""创建不可变 RCA 人工反馈表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0027"
down_revision: str | None = "20260703_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rca_feedback",
        sa.Column("feedback_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=64), nullable=False),
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("corrected_root_cause", sa.Text(), nullable=True),
        sa.Column(
            "missing_evidence_types_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "unsafe_recommendation_indexes_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column("follow_up_label", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column(
            "idempotency_key_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "verdict IN ('ACCEPTED', 'PARTIAL', 'REJECTED')",
            name=op.f("ck_rca_feedback_valid_rca_feedback_verdict"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.workflow_run_id"],
            name=op.f("fk_rca_feedback_workflow_run_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["rca_reports.report_id"],
            name=op.f("fk_rca_feedback_report_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "feedback_id",
            name=op.f("pk_rca_feedback"),
        ),
    )
    op.create_index(
        "uq_rca_feedback_tenant_idempotency",
        "rca_feedback",
        ["tenant_id", "idempotency_key_hash"],
        unique=True,
    )
    op.create_index(
        "ix_rca_feedback_tenant_workflow_created",
        "rca_feedback",
        ["tenant_id", "workflow_run_id", "created_at", "feedback_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rca_feedback_tenant_workflow_created",
        table_name="rca_feedback",
    )
    op.drop_index(
        "uq_rca_feedback_tenant_idempotency",
        table_name="rca_feedback",
    )
    op.drop_table("rca_feedback")
