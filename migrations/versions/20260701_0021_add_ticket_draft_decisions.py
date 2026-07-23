"""增加 Ticket Draft 人工确认状态和幂等字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0021"
down_revision: str | None = "20260701_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展状态约束并增加人工确认事实字段。"""
    with op.batch_alter_table(
        "ticket_drafts",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.add_column(
            sa.Column("decided_by", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column("decision_reason", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "decided_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "decision_idempotency_key_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "decision_request_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "decision_trace_id",
                sa.String(length=128),
                nullable=True,
            )
        )
        batch.drop_constraint(
            batch.f("ck_ticket_drafts_valid_ticket_draft_status"),
            type_="check",
        )
        batch.drop_constraint(
            batch.f("ck_ticket_drafts_initial_ticket_draft_version"),
            type_="check",
        )
        batch.create_check_constraint(
            batch.f("ck_ticket_drafts_valid_ticket_draft_status"),
            "status IN ('DRAFT', 'APPROVED', 'REJECTED')",
        )
        batch.create_check_constraint(
            batch.f("ck_ticket_drafts_valid_ticket_draft_version"),
            "version IN (1, 2)",
        )
        batch.create_check_constraint(
            batch.f("ck_ticket_drafts_valid_ticket_decision_state"),
            "(status = 'DRAFT' AND version = 1 "
            "AND decided_by IS NULL AND decision_reason IS NULL "
            "AND decided_at IS NULL "
            "AND decision_idempotency_key_hash IS NULL "
            "AND decision_request_hash IS NULL "
            "AND decision_trace_id IS NULL) OR "
            "(status = 'APPROVED' AND version = 2 "
            "AND decided_by IS NOT NULL AND decision_reason IS NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_idempotency_key_hash IS NOT NULL "
            "AND decision_request_hash IS NOT NULL "
            "AND decision_trace_id IS NOT NULL) OR "
            "(status = 'REJECTED' AND version = 2 "
            "AND decided_by IS NOT NULL AND decision_reason IS NOT NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_idempotency_key_hash IS NOT NULL "
            "AND decision_request_hash IS NOT NULL "
            "AND decision_trace_id IS NOT NULL)",
        )
    op.create_index(
        "uq_ticket_drafts_tenant_decision_idempotency",
        "ticket_drafts",
        ["tenant_id", "decision_idempotency_key_hash"],
        unique=True,
    )


def downgrade() -> None:
    """删除人工确认字段并恢复仅草稿状态约束。"""
    op.drop_index(
        "uq_ticket_drafts_tenant_decision_idempotency",
        table_name="ticket_drafts",
    )
    with op.batch_alter_table(
        "ticket_drafts",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.drop_constraint(
            batch.f("ck_ticket_drafts_valid_ticket_decision_state"),
            type_="check",
        )
        batch.drop_constraint(
            batch.f("ck_ticket_drafts_valid_ticket_draft_version"),
            type_="check",
        )
        batch.drop_constraint(
            batch.f("ck_ticket_drafts_valid_ticket_draft_status"),
            type_="check",
        )
        batch.create_check_constraint(
            batch.f("ck_ticket_drafts_valid_ticket_draft_status"),
            "status = 'DRAFT'",
        )
        batch.create_check_constraint(
            batch.f("ck_ticket_drafts_initial_ticket_draft_version"),
            "version = 1",
        )
        batch.drop_column("decision_trace_id")
        batch.drop_column("decision_request_hash")
        batch.drop_column("decision_idempotency_key_hash")
        batch.drop_column("decided_at")
        batch.drop_column("decision_reason")
        batch.drop_column("decided_by")
