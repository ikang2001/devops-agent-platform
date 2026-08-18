"""create online dataset release workflow"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_0035"
down_revision = "20260818_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_releases",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("release_id", sa.String(64), primary_key=True),
        sa.Column("dataset_id", sa.String(128), nullable=False),
        sa.Column("source_version", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("dataset_json", sa.Text(), nullable=False),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        sa.Column("candidate_sha256", sa.String(64), nullable=False),
        sa.Column("curation_review_sha256", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("create_idempotency_hash", sa.String(64), nullable=False),
        sa.Column("create_request_hash", sa.String(64), nullable=False),
        sa.Column("publish_idempotency_hash", sa.String(64), nullable=True),
        sa.Column("publish_request_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "version",
            name="uq_dataset_release_version",
        ),
    )
    op.create_index(
        "ix_dataset_releases_tenant_status",
        "dataset_releases",
        ["tenant_id", "status"],
    )
    op.create_table(
        "dataset_release_reviews",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("release_id", sa.String(64), primary_key=True),
        sa.Column("reviewer_id", sa.String(128), primary_key=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "release_id",
            "role",
            name="uq_dataset_release_review_role",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "release_id",
            "idempotency_key_hash",
            name="uq_dataset_release_review_idempotency",
        ),
    )


def downgrade() -> None:
    op.drop_table("dataset_release_reviews")
    op.drop_index("ix_dataset_releases_tenant_status", table_name="dataset_releases")
    op.drop_table("dataset_releases")
