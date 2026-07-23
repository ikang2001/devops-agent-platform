"""创建告警事实表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建告警表、领域值检查约束以及租户维度查询索引。"""
    op.create_table(
        "alerts",
        # 业务标识由应用生成，避免依赖单个数据库实例的自增序列。
        sa.Column("alert_id", sa.String(length=64), nullable=False),
        # 所有业务查询必须携带租户字段，防止跨租户读取数据。
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("service_name", sa.String(length=256), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        # 保存原始告警发生时间，不能用记录入库时间替代。
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "fingerprint",
            sa.String(length=256),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        # 数据库约束作为最后防线，阻止绕过应用校验的非法级别入库。
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'CRITICAL')",
            name=op.f("ck_alerts_valid_severity"),
        ),
        sa.PrimaryKeyConstraint("alert_id", name=op.f("pk_alerts")),
    )
    # 指纹索引用于租户内聚合或幂等判断，但暂不施加唯一语义。
    op.create_index(
        "ix_alerts_tenant_fingerprint",
        "alerts",
        ["tenant_id", "fingerprint"],
        unique=False,
    )
    # 时间索引服务租户内时间窗口查询，避免 RCA 扫描整张告警表。
    op.create_index(
        "ix_alerts_tenant_starts_at",
        "alerts",
        ["tenant_id", "starts_at"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖顺序删除索引和告警表。"""
    op.drop_index("ix_alerts_tenant_starts_at", table_name="alerts")
    op.drop_index("ix_alerts_tenant_fingerprint", table_name="alerts")
    op.drop_table("alerts")
