"""持久化告警与事故的关联关系。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0005"
down_revision: str | None = "20260627_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加可空事故外键和租户关联查询索引。"""
    op.add_column(
        "alerts",
        sa.Column("incident_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_alerts_incident_id_incidents",
        "alerts",
        "incidents",
        ["incident_id"],
        ["incident_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_alerts_tenant_incident",
        "alerts",
        ["tenant_id", "incident_id"],
        unique=False,
    )


def downgrade() -> None:
    """按索引、外键、字段的依赖顺序回滚关联结构。"""
    op.drop_index("ix_alerts_tenant_incident", table_name="alerts")
    op.drop_constraint(
        "fk_alerts_incident_id_incidents",
        "alerts",
        type_="foreignkey",
    )
    op.drop_column("alerts", "incident_id")
