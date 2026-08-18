"""把 CHANGE 加入 RCA Evidence 类型约束。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_0030"
down_revision: str | None = "20260817_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVIDENCE_TYPES_WITH_CHANGE = (
    "'METRIC', 'LOG', 'TRACE', 'DEPLOYMENT', 'CHANGE', 'RUNBOOK', 'INCIDENT_HISTORY'"
)
_EVIDENCE_TYPES_BEFORE_CHANGE = (
    "'METRIC', 'LOG', 'TRACE', 'DEPLOYMENT', 'RUNBOOK', 'INCIDENT_HISTORY'"
)


def upgrade() -> None:
    """以显式约束替换方式加入 CHANGE，兼容滚动发布。"""
    op.drop_constraint(
        op.f("ck_evidence_valid_evidence_type"),
        "evidence",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evidence_valid_evidence_type"),
        "evidence",
        f"evidence_type IN ({_EVIDENCE_TYPES_WITH_CHANGE})",
    )


def downgrade() -> None:
    """回滚前要求调用方确认不存在 CHANGE 行。"""
    op.drop_constraint(
        op.f("ck_evidence_valid_evidence_type"),
        "evidence",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evidence_valid_evidence_type"),
        "evidence",
        f"evidence_type IN ({_EVIDENCE_TYPES_BEFORE_CHANGE})",
    )
