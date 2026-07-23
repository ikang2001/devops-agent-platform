"""为工作流增加审计数据清理水位。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260630_0015"
down_revision: str | None = "20260630_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加清理水位及待清理任务扫描索引。"""
    op.add_column(
        "workflow_runs",
        sa.Column(
            "audit_purged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_workflow_runs_valid_audit_purged_at"),
        "workflow_runs",
        "audit_purged_at IS NULL OR "
        "(ended_at IS NOT NULL AND audit_purged_at >= ended_at)",
    )
    op.create_index(
        "ix_workflow_runs_audit_retention",
        "workflow_runs",
        ["ended_at", "workflow_run_id"],
        unique=False,
        postgresql_where=sa.text(
            "audit_purged_at IS NULL "
            "AND status IN ('SUCCEEDED', 'FAILED', 'CANCELED')"
        ),
    )


def downgrade() -> None:
    """删除审计清理水位、约束和扫描索引。"""
    op.drop_index(
        "ix_workflow_runs_audit_retention",
        table_name="workflow_runs",
    )
    op.drop_constraint(
        op.f("ck_workflow_runs_valid_audit_purged_at"),
        "workflow_runs",
        type_="check",
    )
    op.drop_column("workflow_runs", "audit_purged_at")
