"""create tenant-scoped workspace configuration"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_0034"
down_revision = "20260818_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("workspace_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("prometheus_target", sa.String(512), nullable=False),
        sa.Column("loki_target", sa.String(512), nullable=False),
        sa.Column("tempo_target", sa.String(512), nullable=False),
        sa.Column("knowledge_scope", sa.String(128), nullable=False),
        sa.Column("investigation_policy", sa.String(64), nullable=False),
        sa.Column("allowed_tools_json", sa.Text(), nullable=False),
        sa.Column("llm_provider_policy", sa.String(128), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_workspace_tenant_name"),
    )
    op.create_index(
        "ix_workspaces_tenant_updated", "workspaces", ["tenant_id", "updated_at"]
    )
    op.create_table(
        "workspace_operations",
        sa.Column("operation_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key_hash",
            name="uq_workspace_operation_idempotency",
        ),
    )
    op.create_index(
        "ix_workspace_operations_target",
        "workspace_operations",
        ["tenant_id", "workspace_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_operations_target",
        table_name="workspace_operations",
    )
    op.drop_table("workspace_operations")
    op.drop_index("ix_workspaces_tenant_updated", table_name="workspaces")
    op.drop_table("workspaces")
