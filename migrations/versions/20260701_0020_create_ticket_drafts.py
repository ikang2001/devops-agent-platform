"""创建 RCA 派生的本地工单草稿表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0020"
down_revision: str | None = "20260701_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建草稿事实、来源外键、业务唯一键和查询索引。"""
    op.create_table(
        "ticket_drafts",
        sa.Column(
            "ticket_draft_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column(
            "workflow_run_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("recommendations_json", sa.Text(), nullable=False),
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
        sa.Column(
            "version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.CheckConstraint(
            "status = 'DRAFT'",
            name=op.f("ck_ticket_drafts_valid_ticket_draft_status"),
        ),
        sa.CheckConstraint(
            "priority IN ('P1', 'P2', 'P3')",
            name=op.f("ck_ticket_drafts_valid_ticket_priority"),
        ),
        sa.CheckConstraint(
            "version = 1",
            name=op.f("ck_ticket_drafts_initial_ticket_draft_version"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_ticket_drafts_incident_id_incidents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.workflow_run_id"],
            name="fk_ticket_drafts_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["rca_reports.report_id"],
            name="fk_ticket_drafts_report_id_rca_reports",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "ticket_draft_id",
            name=op.f("pk_ticket_drafts"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_run_id",
            name=op.f("uq_ticket_drafts_tenant_workflow"),
        ),
    )
    op.create_index(
        "uq_ticket_drafts_tenant_idempotency",
        "ticket_drafts",
        ["tenant_id", "idempotency_key_hash"],
        unique=True,
    )
    op.create_index(
        "ix_ticket_drafts_tenant_incident_created",
        "ticket_drafts",
        [
            "tenant_id",
            "incident_id",
            "created_at",
            "ticket_draft_id",
        ],
        unique=False,
    )


def downgrade() -> None:
    """删除本地工单草稿表及其索引。"""
    op.drop_index(
        "ix_ticket_drafts_tenant_incident_created",
        table_name="ticket_drafts",
    )
    op.drop_index(
        "uq_ticket_drafts_tenant_idempotency",
        table_name="ticket_drafts",
    )
    op.drop_table("ticket_drafts")
