"""持久化结构化根因候选与最终选择结果。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_0039"
down_revision: str | None = "20260818_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_WITH_NO_ACTION = (
    "'UNDETERMINED', 'CANDIDATE', 'CONFIRMED', 'NO_ACTIONABLE_ROOT_CAUSE'"
)
_LEGACY_STATUS = "'UNDETERMINED', 'CANDIDATE', 'CONFIRMED'"


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_rca_reports_valid_rca_conclusion_status"),
        "rca_reports",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_rca_reports_valid_rca_conclusion_status"),
        "rca_reports",
        f"conclusion_status IN ({_STATUS_WITH_NO_ACTION})",
    )
    op.add_column(
        "rca_reports",
        sa.Column("root_cause_type", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "rca_reports",
        sa.Column("root_cause_resource", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "rca_reports",
        sa.Column("selected_candidate_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "rca_reports",
        sa.Column(
            "root_cause_candidates_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE rca_reports SET conclusion_status = 'UNDETERMINED' "
        "WHERE conclusion_status = 'NO_ACTIONABLE_ROOT_CAUSE'"
    )
    op.drop_column("rca_reports", "root_cause_candidates_json")
    op.drop_column("rca_reports", "selected_candidate_id")
    op.drop_column("rca_reports", "root_cause_resource")
    op.drop_column("rca_reports", "root_cause_type")
    op.drop_constraint(
        op.f("ck_rca_reports_valid_rca_conclusion_status"),
        "rca_reports",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_rca_reports_valid_rca_conclusion_status"),
        "rca_reports",
        f"conclusion_status IN ({_LEGACY_STATUS})",
    )
