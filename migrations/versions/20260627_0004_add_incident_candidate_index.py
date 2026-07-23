"""增加事故候选查询复合索引。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260627_0004"
down_revision: str | None = "20260627_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建与候选过滤前缀和稳定排序一致的索引。"""
    op.create_index(
        "ix_incidents_candidate_lookup",
        "incidents",
        [
            "tenant_id",
            "service_name",
            "updated_at",
            "incident_id",
        ],
        unique=False,
    )


def downgrade() -> None:
    """删除事故候选查询索引。"""
    op.drop_index(
        "ix_incidents_candidate_lookup",
        table_name="incidents",
    )
