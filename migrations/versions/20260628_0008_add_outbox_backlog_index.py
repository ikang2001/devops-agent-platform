"""增加Outbox积压监控部分索引。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_0008"
down_revision: str | None = "20260627_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """仅为未发布和失败记录创建状态时间索引。"""
    op.create_index(
        "ix_outbox_backlog_status_created",
        "outbox_events",
        ["status", "created_at"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('PENDING', 'PROCESSING', 'FAILED')"
        ),
    )


def downgrade() -> None:
    """移除Outbox积压监控索引。"""
    op.drop_index(
        "ix_outbox_backlog_status_created",
        table_name="outbox_events",
    )
