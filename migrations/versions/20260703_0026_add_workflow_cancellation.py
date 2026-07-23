"""增加 WorkflowRun 管理端取消事实和幂等字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260703_0026"
down_revision: str | None = "20260702_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为管理端取消工作流增加可审计、可幂等的可空字段。"""
    with op.batch_alter_table(
        "workflow_runs",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.add_column(
            sa.Column("canceled_by", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "cancellation_reason",
                sa.String(length=2048),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "canceled_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "cancellation_idempotency_key_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "cancellation_request_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "cancellation_trace_id",
                sa.String(length=128),
                nullable=True,
            )
        )
        batch.create_check_constraint(
            batch.f("ck_workflow_runs_valid_cancellation_state"),
            "(canceled_by IS NULL AND cancellation_reason IS NULL "
            "AND canceled_at IS NULL "
            "AND cancellation_idempotency_key_hash IS NULL "
            "AND cancellation_request_hash IS NULL "
            "AND cancellation_trace_id IS NULL) OR "
            "(status = 'CANCELED' "
            "AND canceled_by IS NOT NULL "
            "AND cancellation_reason IS NOT NULL "
            "AND canceled_at IS NOT NULL "
            "AND cancellation_idempotency_key_hash IS NOT NULL "
            "AND cancellation_request_hash IS NOT NULL "
            "AND cancellation_trace_id IS NOT NULL "
            "AND canceled_at = ended_at)",
        )
    op.create_index(
        "uq_workflow_runs_tenant_cancellation_idempotency",
        "workflow_runs",
        ["tenant_id", "cancellation_idempotency_key_hash"],
        unique=True,
    )


def downgrade() -> None:
    """删除工作流管理端取消事实字段和约束。"""
    op.drop_index(
        "uq_workflow_runs_tenant_cancellation_idempotency",
        table_name="workflow_runs",
    )
    with op.batch_alter_table(
        "workflow_runs",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.drop_constraint(
            batch.f("ck_workflow_runs_valid_cancellation_state"),
            type_="check",
        )
        batch.drop_column("cancellation_trace_id")
        batch.drop_column("cancellation_request_hash")
        batch.drop_column("cancellation_idempotency_key_hash")
        batch.drop_column("canceled_at")
        batch.drop_column("cancellation_reason")
        batch.drop_column("canceled_by")
