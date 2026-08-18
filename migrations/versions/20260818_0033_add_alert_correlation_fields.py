"""add deterministic alert correlation metadata"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_0033"
down_revision = "20260818_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column(
            "environment", sa.String(64), nullable=False, server_default="default"
        ),
    )
    op.add_column(
        "alerts",
        sa.Column(
            "alert_type", sa.String(128), nullable=False, server_default="generic"
        ),
    )
    op.add_column(
        "alerts",
        sa.Column("labels_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "incidents", sa.Column("primary_alert_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "incidents",
        sa.Column(
            "correlated_alert_count", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.add_column(
        "incidents",
        sa.Column(
            "correlation_reason",
            sa.String(512),
            nullable=False,
            server_default="PRIMARY_ALERT",
        ),
    )
    op.create_index(
        "ix_alerts_tenant_environment_created",
        "alerts",
        ["tenant_id", "environment", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_tenant_environment_created", table_name="alerts")
    for table, column in (
        ("incidents", "correlation_reason"),
        ("incidents", "correlated_alert_count"),
        ("incidents", "primary_alert_id"),
        ("alerts", "labels_json"),
        ("alerts", "alert_type"),
        ("alerts", "environment"),
    ):
        op.drop_column(table, column)
