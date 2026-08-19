"""persist incident environment and correlated service set"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0037"
down_revision: str | None = "20260818_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column(
            "environment",
            sa.String(length=64),
            nullable=False,
            server_default="default",
        ),
    )
    op.add_column(
        "incidents",
        sa.Column(
            "affected_services_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.create_index(
        "ix_incidents_tenant_environment_updated",
        "incidents",
        ["tenant_id", "environment", "updated_at", "incident_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incidents_tenant_environment_updated",
        table_name="incidents",
    )
    op.drop_column("incidents", "affected_services_json")
    op.drop_column("incidents", "environment")
