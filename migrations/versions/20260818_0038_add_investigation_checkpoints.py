"""persist bounded dynamic investigation checkpoints"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0038"
down_revision: str | None = "20260818_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investigation_checkpoints",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "incident_id"),
    )
    op.create_index(
        "ix_investigation_checkpoints_tenant_updated",
        "investigation_checkpoints",
        ["tenant_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_investigation_checkpoints_tenant_updated",
        table_name="investigation_checkpoints",
    )
    op.drop_table("investigation_checkpoints")
