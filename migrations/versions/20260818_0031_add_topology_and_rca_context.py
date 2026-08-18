"""add topology graph and structured RCA context

Revision ID: 20260818_0031
Revises: 20260817_0030
"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_0031"
down_revision = "20260817_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topology_nodes",
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("node_kind", sa.String(32), nullable=False),
        sa.Column("service_name", sa.String(256), nullable=True),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_name", sa.String(256), nullable=True),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="valid_topology_node_confidence"
        ),
    )
    op.create_index(
        "ix_topology_nodes_tenant_environment",
        "topology_nodes",
        ["tenant_id", "environment"],
    )
    op.create_table(
        "topology_edges",
        sa.Column("edge_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("source_node_id", sa.String(128), nullable=False),
        sa.Column("target_node_id", sa.String(128), nullable=False),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_node_id"], ["topology_nodes.node_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id"], ["topology_nodes.node_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_node_id",
            "target_node_id",
            "relation",
            name="uq_topology_edge_key",
        ),
        sa.CheckConstraint(
            "source_node_id <> target_node_id", name="topology_edge_not_self_loop"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="valid_topology_edge_confidence"
        ),
    )
    op.create_index(
        "ix_topology_edges_tenant_source",
        "topology_edges",
        ["tenant_id", "source_node_id"],
    )
    op.add_column(
        "rca_reports", sa.Column("suspected_root_node", sa.String(256), nullable=True)
    )
    op.add_column(
        "rca_reports",
        sa.Column("causal_chain_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "rca_reports",
        sa.Column(
            "affected_services_json", sa.Text(), nullable=False, server_default="[]"
        ),
    )
    op.add_column(
        "rca_reports",
        sa.Column("blast_radius_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("rca_reports", "blast_radius_json")
    op.drop_column("rca_reports", "affected_services_json")
    op.drop_column("rca_reports", "causal_chain_json")
    op.drop_column("rca_reports", "suspected_root_node")
    op.drop_index("ix_topology_edges_tenant_source", table_name="topology_edges")
    op.drop_table("topology_edges")
    op.drop_index("ix_topology_nodes_tenant_environment", table_name="topology_nodes")
    op.drop_table("topology_nodes")
