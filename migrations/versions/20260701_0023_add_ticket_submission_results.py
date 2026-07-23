"""增加外部工单提交结果字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0023"
down_revision: str | None = "20260701_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展提交请求终态、结果字段和结果幂等索引。"""
    with op.batch_alter_table(
        "ticket_submissions",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.add_column(
            sa.Column(
                "external_ticket_id",
                sa.String(length=256),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "external_ticket_url",
                sa.String(length=2048),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("failure_reason", sa.String(length=2048), nullable=True)
        )
        batch.add_column(
            sa.Column("completed_by", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "completed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "result_idempotency_key_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "result_request_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "result_trace_id",
                sa.String(length=128),
                nullable=True,
            )
        )
        batch.drop_constraint(
            batch.f("ck_ticket_submissions_valid_ticket_submission_status"),
            type_="check",
        )
        batch.drop_constraint(
            batch.f("ck_ticket_submissions_initial_ticket_submission_version"),
            type_="check",
        )
        batch.create_check_constraint(
            batch.f("ck_ticket_submissions_valid_ticket_submission_status"),
            "status IN ('REQUESTED', 'SUBMITTED', 'FAILED')",
        )
        batch.create_check_constraint(
            batch.f("ck_ticket_submissions_valid_ticket_submission_version"),
            "version IN (1, 2)",
        )
        batch.create_check_constraint(
            batch.f(
                "ck_ticket_submissions_valid_ticket_submission_result_state"
            ),
            "(status = 'REQUESTED' AND version = 1 "
            "AND external_ticket_id IS NULL "
            "AND external_ticket_url IS NULL "
            "AND failure_reason IS NULL "
            "AND completed_by IS NULL "
            "AND completed_at IS NULL "
            "AND result_idempotency_key_hash IS NULL "
            "AND result_request_hash IS NULL "
            "AND result_trace_id IS NULL) OR "
            "(status = 'SUBMITTED' AND version = 2 "
            "AND external_ticket_id IS NOT NULL "
            "AND failure_reason IS NULL "
            "AND completed_by IS NOT NULL "
            "AND completed_at IS NOT NULL "
            "AND result_idempotency_key_hash IS NOT NULL "
            "AND result_request_hash IS NOT NULL "
            "AND result_trace_id IS NOT NULL) OR "
            "(status = 'FAILED' AND version = 2 "
            "AND external_ticket_id IS NULL "
            "AND external_ticket_url IS NULL "
            "AND failure_reason IS NOT NULL "
            "AND completed_by IS NOT NULL "
            "AND completed_at IS NOT NULL "
            "AND result_idempotency_key_hash IS NOT NULL "
            "AND result_request_hash IS NOT NULL "
            "AND result_trace_id IS NOT NULL)",
        )
    op.create_index(
        "uq_ticket_submissions_tenant_result_idempotency",
        "ticket_submissions",
        ["tenant_id", "result_idempotency_key_hash"],
        unique=True,
    )
    op.create_index(
        "uq_ticket_submissions_external_ticket",
        "ticket_submissions",
        ["tenant_id", "target_system", "external_ticket_id"],
        unique=True,
        postgresql_where=sa.text("external_ticket_id IS NOT NULL"),
        sqlite_where=sa.text("external_ticket_id IS NOT NULL"),
    )


def downgrade() -> None:
    """删除外部提交结果字段并恢复仅请求状态。"""
    op.drop_index(
        "uq_ticket_submissions_external_ticket",
        table_name="ticket_submissions",
    )
    op.drop_index(
        "uq_ticket_submissions_tenant_result_idempotency",
        table_name="ticket_submissions",
    )
    with op.batch_alter_table(
        "ticket_submissions",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.drop_constraint(
            batch.f(
                "ck_ticket_submissions_valid_ticket_submission_result_state"
            ),
            type_="check",
        )
        batch.drop_constraint(
            batch.f("ck_ticket_submissions_valid_ticket_submission_version"),
            type_="check",
        )
        batch.drop_constraint(
            batch.f("ck_ticket_submissions_valid_ticket_submission_status"),
            type_="check",
        )
        batch.create_check_constraint(
            batch.f("ck_ticket_submissions_valid_ticket_submission_status"),
            "status = 'REQUESTED'",
        )
        batch.create_check_constraint(
            batch.f(
                "ck_ticket_submissions_initial_ticket_submission_version"
            ),
            "version = 1",
        )
        batch.drop_column("result_trace_id")
        batch.drop_column("result_request_hash")
        batch.drop_column("result_idempotency_key_hash")
        batch.drop_column("completed_at")
        batch.drop_column("completed_by")
        batch.drop_column("failure_reason")
        batch.drop_column("external_ticket_url")
        batch.drop_column("external_ticket_id")
