"""为告警接入增加上游事件幂等键。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0003"
down_revision: str | None = "20260627_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """通过扩展、回填、收紧三步兼容已有告警数据。"""
    # 第一步先允许为空，避免已有数据导致 ADD COLUMN 直接失败。
    op.add_column(
        "alerts",
        sa.Column("external_event_id", sa.String(length=256), nullable=True),
    )
    # 历史数据没有上游事件 ID，使用告警主键生成不会碰撞的保留值。
    op.execute(
        sa.text(
            "UPDATE alerts "
            "SET external_event_id = 'legacy:' || alert_id "
            "WHERE external_event_id IS NULL"
        )
    )
    # 回填完成后再收紧非空约束，并建立数据库级并发防线。
    op.alter_column(
        "alerts",
        "external_event_id",
        existing_type=sa.String(length=256),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_alerts_tenant_source_external_event",
        "alerts",
        ["tenant_id", "source", "external_event_id"],
    )


def downgrade() -> None:
    """删除幂等唯一约束和上游事件字段。"""
    op.drop_constraint(
        "uq_alerts_tenant_source_external_event",
        "alerts",
        type_="unique",
    )
    op.drop_column("alerts", "external_event_id")
