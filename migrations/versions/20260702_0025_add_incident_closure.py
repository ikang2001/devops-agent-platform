"""增加 Incident 人工关闭事实和幂等字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260702_0025"
down_revision: str | None = "20260702_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加兼容历史关闭态的可空关闭字段和一致性约束。"""
    with op.batch_alter_table(
        "incidents",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.add_column(
            sa.Column("closed_by", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column("closure_reason", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "closed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "closure_idempotency_key_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "closure_request_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "closure_trace_id",
                sa.String(length=128),
                nullable=True,
            )
        )
        batch.create_check_constraint(
            batch.f("ck_incidents_valid_incident_closure_state"),
            "(closed_by IS NULL AND closure_reason IS NULL "
            "AND closed_at IS NULL "
            "AND closure_idempotency_key_hash IS NULL "
            "AND closure_request_hash IS NULL "
            "AND closure_trace_id IS NULL) OR "
            "(status = 'CLOSED' "
            "AND closed_by IS NOT NULL "
            "AND closure_reason IS NOT NULL "
            "AND closed_at IS NOT NULL "
            "AND closure_idempotency_key_hash IS NOT NULL "
            "AND closure_request_hash IS NOT NULL "
            "AND closure_trace_id IS NOT NULL)",
        )
        batch.create_check_constraint(
            batch.f("ck_incidents_valid_incident_closure_time"),
            "closed_at IS NULL OR "
            "(closed_at >= created_at AND closed_at <= updated_at)",
        )
    op.create_index(
        "uq_incidents_tenant_closure_idempotency",
        "incidents",
        ["tenant_id", "closure_idempotency_key_hash"],
        unique=True,
    )


def downgrade() -> None:
    """删除事故关闭事实字段和约束。"""
    op.drop_index(
        "uq_incidents_tenant_closure_idempotency",
        table_name="incidents",
    )
    with op.batch_alter_table(
        "incidents",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.drop_constraint(
            batch.f("ck_incidents_valid_incident_closure_time"),
            type_="check",
        )
        batch.drop_constraint(
            batch.f("ck_incidents_valid_incident_closure_state"),
            type_="check",
        )
        batch.drop_column("closure_trace_id")
        batch.drop_column("closure_request_hash")
        batch.drop_column("closure_idempotency_key_hash")
        batch.drop_column("closed_at")
        batch.drop_column("closure_reason")
        batch.drop_column("closed_by")
