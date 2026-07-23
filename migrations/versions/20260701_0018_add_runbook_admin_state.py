"""增加 Runbook 草稿并发版本、发布时刻和管理幂等记录。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0018"
down_revision: str | None = "20260701_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展 Runbook 生命周期并创建管理操作表。"""
    op.add_column(
        "runbooks",
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "runbooks",
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE runbooks SET published_at = updated_at "
            "WHERE status IN ('PUBLISHED', 'ARCHIVED')"
        )
    )
    op.create_check_constraint(
        op.f("ck_runbooks_positive_runbook_revision"),
        "runbooks",
        "revision >= 1",
    )
    op.create_check_constraint(
        op.f("ck_runbooks_valid_runbook_published_at"),
        "runbooks",
        "(status = 'DRAFT' AND published_at IS NULL) OR "
        "(status IN ('PUBLISHED', 'ARCHIVED') "
        "AND published_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_runbooks_valid_runbook_timeline"),
        "runbooks",
        "published_at IS NULL OR published_at <= updated_at",
    )

    op.create_table(
        "runbook_operations",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("runbook_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "idempotency_key_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column(
            "result_runbook_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("result_status", sa.String(length=32), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('SAVE_DRAFT', 'PUBLISH')",
            name=op.f(
                "ck_runbook_operations_valid_runbook_operation_action"
            ),
        ),
        sa.CheckConstraint(
            "result_revision >= 1",
            name=op.f(
                "ck_runbook_operations_positive_runbook_result_revision"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["result_runbook_id"],
            ["runbooks.runbook_id"],
            name="fk_runbook_operations_result_runbook_runbooks",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "operation_id",
            name=op.f("pk_runbook_operations"),
        ),
    )
    op.create_index(
        "uq_runbook_operations_tenant_idempotency",
        "runbook_operations",
        ["tenant_id", "idempotency_key_hash"],
        unique=True,
    )
    op.create_index(
        "ix_runbook_operations_key_history",
        "runbook_operations",
        ["tenant_id", "runbook_key", "occurred_at", "operation_id"],
        unique=False,
    )


def downgrade() -> None:
    """删除管理记录并回退 Runbook 生命周期字段。"""
    op.drop_index(
        "ix_runbook_operations_key_history",
        table_name="runbook_operations",
    )
    op.drop_index(
        "uq_runbook_operations_tenant_idempotency",
        table_name="runbook_operations",
    )
    op.drop_table("runbook_operations")
    op.drop_constraint(
        op.f("ck_runbooks_valid_runbook_timeline"),
        "runbooks",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_runbooks_valid_runbook_published_at"),
        "runbooks",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_runbooks_positive_runbook_revision"),
        "runbooks",
        type_="check",
    )
    op.drop_column("runbooks", "published_at")
    op.drop_column("runbooks", "revision")
