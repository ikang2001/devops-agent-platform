"""增加 Outbox 发布租约和处理中状态。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0007"
down_revision: str | None = "20260627_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展状态约束并增加Worker租约字段和恢复索引。"""
    op.drop_constraint(
        op.f("ck_outbox_events_valid_status"),
        "outbox_events",
        type_="check",
    )
    op.add_column(
        "outbox_events",
        sa.Column("lock_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_valid_status"),
        "outbox_events",
        "status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED')",
    )
    op.create_index(
        "ix_outbox_recovery",
        "outbox_events",
        ["status", "locked_until", "event_id"],
        unique=False,
    )


def downgrade() -> None:
    """把处理中记录退回待发布，再移除租约结构。"""
    op.execute(
        sa.text(
            "UPDATE outbox_events "
            "SET status = 'PENDING', lock_id = NULL, locked_until = NULL "
            "WHERE status = 'PROCESSING'"
        )
    )
    op.drop_index("ix_outbox_recovery", table_name="outbox_events")
    op.drop_constraint(
        op.f("ck_outbox_events_valid_status"),
        "outbox_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_valid_status"),
        "outbox_events",
        "status IN ('PENDING', 'PUBLISHED', 'FAILED')",
    )
    op.drop_column("outbox_events", "locked_until")
    op.drop_column("outbox_events", "lock_id")
