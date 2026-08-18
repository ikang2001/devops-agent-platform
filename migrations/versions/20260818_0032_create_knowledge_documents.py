"""create tenant-scoped historical knowledge documents"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_0032"
down_revision = "20260818_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("document_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("service_name", sa.String(256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source_incident_id", sa.String(64), nullable=True),
        sa.Column("error_fingerprint", sa.String(256), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_knowledge_documents_tenant_status_service",
        "knowledge_documents",
        ["tenant_id", "review_status", "service_name"],
    )
    op.create_index(
        "ix_knowledge_documents_tenant_fingerprint",
        "knowledge_documents",
        ["tenant_id", "error_fingerprint"],
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("chunk_id", sa.String(64), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(64),
            sa.ForeignKey("knowledge_documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "document_id", "ordinal", name="uq_knowledge_chunk_ordinal"
        ),
    )


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_index(
        "ix_knowledge_documents_tenant_fingerprint", table_name="knowledge_documents"
    )
    op.drop_index(
        "ix_knowledge_documents_tenant_status_service", table_name="knowledge_documents"
    )
    op.drop_table("knowledge_documents")
