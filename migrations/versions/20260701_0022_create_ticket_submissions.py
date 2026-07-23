"""创建外部工单提交请求表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0022"
down_revision: str | None = "20260701_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建提交请求事实、幂等唯一键和目标系统唯一键。"""
    op.create_table(
        "ticket_submissions",
        sa.Column(
            "ticket_submission_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column(
            "ticket_draft_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "workflow_run_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("target_system", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "idempotency_key_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.CheckConstraint(
            "status = 'REQUESTED'",
            name=op.f(
                "ck_ticket_submissions_valid_ticket_submission_status"
            ),
        ),
        sa.CheckConstraint(
            "version = 1",
            name=op.f(
                "ck_ticket_submissions_initial_ticket_submission_version"
            ),
        ),
        sa.CheckConstraint(
            "length(trim(target_system)) > 0",
            name=op.f(
                "ck_ticket_submissions_non_empty_ticket_submission_target"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_draft_id"],
            ["ticket_drafts.ticket_draft_id"],
            name="fk_ticket_submissions_ticket_draft_id_ticket_drafts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.workflow_run_id"],
            name="fk_ticket_submissions_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "ticket_submission_id",
            name=op.f("pk_ticket_submissions"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "ticket_draft_id",
            "target_system",
            name=op.f(
                "uq_ticket_submissions_tenant_draft_target"
            ),
        ),
    )
    op.create_index(
        "uq_ticket_submissions_tenant_idempotency",
        "ticket_submissions",
        ["tenant_id", "idempotency_key_hash"],
        unique=True,
    )
    op.create_index(
        "ix_ticket_submissions_tenant_workflow_status",
        "ticket_submissions",
        [
            "tenant_id",
            "workflow_run_id",
            "status",
            "requested_at",
            "ticket_submission_id",
        ],
        unique=False,
    )


def downgrade() -> None:
    """删除外部工单提交请求表及其索引。"""
    op.drop_index(
        "ix_ticket_submissions_tenant_workflow_status",
        table_name="ticket_submissions",
    )
    op.drop_index(
        "uq_ticket_submissions_tenant_idempotency",
        table_name="ticket_submissions",
    )
    op.drop_table("ticket_submissions")
