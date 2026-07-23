"""创建 Runbook 聚合头并发控制表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0019"
down_revision: str | None = "20260701_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建稳定聚合锁、修订号和当前快照指针。"""
    op.create_table(
        "runbook_heads",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("runbook_key", sa.String(length=128), nullable=False),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "draft_runbook_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "published_runbook_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name=op.f(
                "ck_runbook_heads_non_negative_runbook_head_revision"
            ),
        ),
        sa.CheckConstraint(
            "draft_runbook_id IS NULL OR published_runbook_id IS NULL "
            "OR draft_runbook_id <> published_runbook_id",
            name=op.f(
                "ck_runbook_heads_distinct_runbook_head_pointers"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["draft_runbook_id"],
            ["runbooks.runbook_id"],
            name="fk_runbook_heads_draft_runbook_runbooks",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["published_runbook_id"],
            ["runbooks.runbook_id"],
            name="fk_runbook_heads_published_runbook_runbooks",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "runbook_key",
            name=op.f("pk_runbook_heads"),
        ),
    )


def downgrade() -> None:
    """删除 Runbook 聚合头表。"""
    op.drop_table("runbook_heads")
