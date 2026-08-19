"""allow topology and historical knowledge as first-class RCA evidence"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260818_0036"
down_revision: str | None = "20260818_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVIDENCE_TYPES_WITH_CONTEXT = (
    "'METRIC', 'LOG', 'TRACE', 'DEPLOYMENT', 'CHANGE', 'RUNBOOK', "
    "'INCIDENT_HISTORY', 'TOPOLOGY', 'KNOWLEDGE'"
)
_EVIDENCE_TYPES_WITHOUT_CONTEXT = (
    "'METRIC', 'LOG', 'TRACE', 'DEPLOYMENT', 'CHANGE', 'RUNBOOK', "
    "'INCIDENT_HISTORY'"
)


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_evidence_valid_evidence_type"),
        "evidence",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evidence_valid_evidence_type"),
        "evidence",
        f"evidence_type IN ({_EVIDENCE_TYPES_WITH_CONTEXT})",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_evidence_valid_evidence_type"),
        "evidence",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evidence_valid_evidence_type"),
        "evidence",
        f"evidence_type IN ({_EVIDENCE_TYPES_WITHOUT_CONTEXT})",
    )
