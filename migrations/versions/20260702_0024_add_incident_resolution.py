"""增加 Incident 人工解决事实和幂等字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260702_0024"
down_revision: str | None = "20260701_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加兼容历史终态的可空解决字段和一致性约束。"""
    with op.batch_alter_table(
        "incidents",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.add_column(
            sa.Column("resolved_by", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column("resolution_reason", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "resolved_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "resolution_idempotency_key_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "resolution_request_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "resolution_trace_id",
                sa.String(length=128),
                nullable=True,
            )
        )
        batch.create_check_constraint(
            batch.f("ck_incidents_valid_incident_resolution_state"),
            "(resolved_by IS NULL AND resolution_reason IS NULL "
            "AND resolved_at IS NULL "
            "AND resolution_idempotency_key_hash IS NULL "
            "AND resolution_request_hash IS NULL "
            "AND resolution_trace_id IS NULL) OR "
            "(status IN ('RESOLVED', 'CLOSED') "
            "AND resolved_by IS NOT NULL "
            "AND resolution_reason IS NOT NULL "
            "AND resolved_at IS NOT NULL "
            "AND resolution_idempotency_key_hash IS NOT NULL "
            "AND resolution_request_hash IS NOT NULL "
            "AND resolution_trace_id IS NOT NULL)",
        )
        batch.create_check_constraint(
            batch.f("ck_incidents_valid_incident_resolution_time"),
            "resolved_at IS NULL OR "
            "(resolved_at >= created_at AND resolved_at <= updated_at)",
        )
    op.create_index(
        "uq_incidents_tenant_resolution_idempotency",
        "incidents",
        ["tenant_id", "resolution_idempotency_key_hash"],
        unique=True,
    )


def downgrade() -> None:
    """删除事故解决事实字段和约束。"""
    op.drop_index(
        "uq_incidents_tenant_resolution_idempotency",
        table_name="incidents",
    )
    with op.batch_alter_table(
        "incidents",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.drop_constraint(
            batch.f("ck_incidents_valid_incident_resolution_time"),
            type_="check",
        )
        batch.drop_constraint(
            batch.f("ck_incidents_valid_incident_resolution_state"),
            type_="check",
        )
        batch.drop_column("resolution_trace_id")
        batch.drop_column("resolution_request_hash")
        batch.drop_column("resolution_idempotency_key_hash")
        batch.drop_column("resolved_at")
        batch.drop_column("resolution_reason")
        batch.drop_column("resolved_by")
