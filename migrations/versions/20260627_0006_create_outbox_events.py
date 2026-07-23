"""创建事务型 Outbox 事件表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260627_0006"
down_revision: str | None = "20260627_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Outbox 表、状态约束和发布轮询索引。"""
    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PUBLISHED', 'FAILED')",
            name=op.f("ck_outbox_events_valid_status"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_outbox_events_positive_schema_version"),
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_outbox_events_non_negative_attempts"),
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        "ix_outbox_dispatch",
        "outbox_events",
        ["status", "available_at", "created_at", "event_id"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_aggregate",
        "outbox_events",
        ["tenant_id", "aggregate_type", "aggregate_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除 Outbox 索引和事件表。"""
    op.drop_index("ix_outbox_aggregate", table_name="outbox_events")
    op.drop_index("ix_outbox_dispatch", table_name="outbox_events")
    op.drop_table("outbox_events")
