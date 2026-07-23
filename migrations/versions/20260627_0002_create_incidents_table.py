"""创建事故聚合表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0002"
down_revision: str | None = "20260627_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建事故表、领域检查约束和租户分页索引。"""
    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("service_name", sa.String(length=256), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # 乐观锁版本从 1 开始，每次成功更新由 ORM 自动递增。
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'CRITICAL')",
            name=op.f("ck_incidents_valid_severity"),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'ANALYZING', 'RESOLVED', 'CLOSED')",
            name=op.f("ck_incidents_valid_status"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_incidents_valid_time_range"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_incidents_positive_version"),
        ),
        sa.PrimaryKeyConstraint("incident_id", name=op.f("pk_incidents")),
    )
    # incident_id 作为最后游标，保证时间相同的数据仍可稳定翻页。
    op.create_index(
        "ix_incidents_tenant_status_updated_id",
        "incidents",
        ["tenant_id", "status", "updated_at", "incident_id"],
        unique=False,
    )
    op.create_index(
        "ix_incidents_tenant_service_created_id",
        "incidents",
        ["tenant_id", "service_name", "created_at", "incident_id"],
        unique=False,
    )


def downgrade() -> None:
    """删除事故索引和数据表。"""
    op.drop_index(
        "ix_incidents_tenant_service_created_id",
        table_name="incidents",
    )
    op.drop_index(
        "ix_incidents_tenant_status_updated_id",
        table_name="incidents",
    )
    op.drop_table("incidents")
