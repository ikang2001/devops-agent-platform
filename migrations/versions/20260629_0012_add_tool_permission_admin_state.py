"""增加权限版本、管理幂等记录和审计索引。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260629_0012"
down_revision: str | None = "20260629_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加权限乐观版本和持久化幂等操作结果。"""
    op.add_column(
        "tool_permission_grants",
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_tool_permission_grants_positive_version"),
        "tool_permission_grants",
        "version >= 1",
    )

    op.create_table(
        "tool_permission_operations",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("operator_id", sa.String(length=128), nullable=False),
        sa.Column(
            "idempotency_key_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "request_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column(
            "result_grant_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column(
            "requested_by",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('SET', 'REVOKE')",
            name=op.f(
                "ck_tool_permission_operations_valid_action"
            ),
        ),
        sa.CheckConstraint(
            "result_version >= 1",
            name=op.f(
                "ck_tool_permission_operations_positive_result_version"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["result_grant_id"],
            ["tool_permission_grants.grant_id"],
            name="fk_permission_ops_grant_permission_grants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "operation_id",
            name=op.f("pk_tool_permission_operations"),
        ),
    )
    op.create_index(
        "uq_tool_permission_operations_tenant_idempotency",
        "tool_permission_operations",
        ["tenant_id", "idempotency_key_hash"],
        unique=True,
    )
    op.create_index(
        "ix_tool_permission_operations_subject_history",
        "tool_permission_operations",
        ["tenant_id", "operator_id", "occurred_at", "operation_id"],
        unique=False,
    )


def downgrade() -> None:
    """删除管理操作表和授权版本字段。"""
    op.drop_index(
        "ix_tool_permission_operations_subject_history",
        table_name="tool_permission_operations",
    )
    op.drop_index(
        "uq_tool_permission_operations_tenant_idempotency",
        table_name="tool_permission_operations",
    )
    op.drop_table("tool_permission_operations")
    op.drop_constraint(
        op.f("ck_tool_permission_grants_positive_version"),
        "tool_permission_grants",
        type_="check",
    )
    op.drop_column("tool_permission_grants", "version")
