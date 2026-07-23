"""创建租户隔离的版本化 Runbook 表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0017"
down_revision: str | None = "20260630_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Runbook 主表和服务检索复合索引。"""
    op.create_table(
        "runbooks",
        sa.Column("runbook_id", sa.String(length=64), nullable=False),
        sa.Column("runbook_key", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("service_name", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "priority",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name=op.f("ck_runbooks_valid_runbook_status"),
        ),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 1000",
            name=op.f("ck_runbooks_valid_runbook_priority"),
        ),
        sa.CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name=op.f("ck_runbooks_non_empty_runbook_tenant"),
        ),
        sa.CheckConstraint(
            "length(trim(service_name)) > 0",
            name=op.f("ck_runbooks_non_empty_runbook_service"),
        ),
        sa.PrimaryKeyConstraint(
            "runbook_id",
            name=op.f("pk_runbooks"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "runbook_key",
            "version",
            name=op.f("uq_runbooks_tenant_key_version"),
        ),
    )
    op.create_index(
        "uq_runbooks_tenant_published_key",
        "runbooks",
        ["tenant_id", "runbook_key"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
        sqlite_where=sa.text("status = 'PUBLISHED'"),
    )
    op.create_index(
        "ix_runbooks_tenant_status_service_priority",
        "runbooks",
        [
            "tenant_id",
            "status",
            "service_name",
            "priority",
            "updated_at",
            "runbook_id",
        ],
        unique=False,
    )


def downgrade() -> None:
    """删除 Runbook 表和检索索引。"""
    op.drop_index(
        "uq_runbooks_tenant_published_key",
        table_name="runbooks",
    )
    op.drop_index(
        "ix_runbooks_tenant_status_service_priority",
        table_name="runbooks",
    )
    op.drop_table("runbooks")
