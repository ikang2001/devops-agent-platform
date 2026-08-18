"""创建租户隔离、可幂等接入和按时间窗口查询的变更事件表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0029"
down_revision: str | None = "20260723_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Change Event 事实表、幂等约束和 RCA 窗口查询索引。"""
    op.create_table(
        "change_events",
        sa.Column("change_event_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("external_event_id", sa.String(length=256), nullable=False),
        sa.Column("service_name", sa.String(length=256), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.String(length=256), nullable=False),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version_before", sa.String(length=256), nullable=True),
        sa.Column("version_after", sa.String(length=256), nullable=True),
        sa.Column("operator_id", sa.String(length=128), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "change_type IN ("
            "'DEPLOYMENT','CONFIG','FEATURE_FLAG','DEPENDENCY','SCHEMA',"
            "'INFRASTRUCTURE','MANUAL_OPERATION')",
            name=op.f("ck_change_events_valid_change_type"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELED')",
            name=op.f("ck_change_events_valid_change_event_status"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name=op.f("ck_change_events_valid_completion_time"),
        ),
        sa.PrimaryKeyConstraint(
            "change_event_id",
            name=op.f("pk_change_events"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source",
            "external_event_id",
            name="uq_change_events_tenant_source_external_event",
        ),
    )
    op.create_index(
        "ix_change_events_tenant_service_started",
        "change_events",
        ["tenant_id", "service_name", "started_at", "change_event_id"],
        unique=False,
    )


def downgrade() -> None:
    """删除 Change Event 查询索引和事实表。"""
    op.drop_index(
        "ix_change_events_tenant_service_started",
        table_name="change_events",
    )
    op.drop_table("change_events")
