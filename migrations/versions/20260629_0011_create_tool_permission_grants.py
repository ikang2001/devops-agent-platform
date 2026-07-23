"""创建工具权限授权主体和标签明细表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260629_0011"
down_revision: str | None = "20260628_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建租户操作者唯一授权、有效期、撤销状态和标签约束。"""
    op.create_table(
        "tool_permission_grants",
        sa.Column("grant_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("operator_id", sa.String(length=128), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name=op.f(
                "ck_tool_permission_grants_non_empty_tenant_id"
            ),
        ),
        sa.CheckConstraint(
            "length(trim(operator_id)) > 0",
            name=op.f(
                "ck_tool_permission_grants_non_empty_operator_id"
            ),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_tool_permission_grants_valid_updated_at"),
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name=op.f("ck_tool_permission_grants_valid_expires_at"),
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name=op.f("ck_tool_permission_grants_valid_revoked_at"),
        ),
        sa.PrimaryKeyConstraint(
            "grant_id",
            name=op.f("pk_tool_permission_grants"),
        ),
    )
    op.create_index(
        "uq_tool_permission_grants_active_operator",
        "tool_permission_grants",
        ["tenant_id", "operator_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_tool_permission_grants_operator_history",
        "tool_permission_grants",
        ["tenant_id", "operator_id", "created_at", "grant_id"],
        unique=False,
    )

    op.create_table(
        "tool_permission_tags",
        sa.Column("grant_id", sa.String(length=64), nullable=False),
        sa.Column(
            "permission_tag",
            sa.String(length=128),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(permission_tag)) > 0",
            name=op.f(
                "ck_tool_permission_tags_non_empty_permission_tag"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["tool_permission_grants.grant_id"],
            name=(
                "fk_tool_permission_tags_grant_id_"
                "tool_permission_grants"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "grant_id",
            "permission_tag",
            name=op.f("pk_tool_permission_tags"),
        ),
    )
    op.create_index(
        "ix_tool_permission_tags_permission_tag",
        "tool_permission_tags",
        ["permission_tag"],
        unique=False,
    )


def downgrade() -> None:
    """按外键依赖顺序删除权限标签和授权主体表。"""
    op.drop_index(
        "ix_tool_permission_tags_permission_tag",
        table_name="tool_permission_tags",
    )
    op.drop_table("tool_permission_tags")
    op.drop_index(
        "ix_tool_permission_grants_operator_history",
        table_name="tool_permission_grants",
    )
    op.drop_index(
        "uq_tool_permission_grants_active_operator",
        table_name="tool_permission_grants",
    )
    op.drop_table("tool_permission_grants")
