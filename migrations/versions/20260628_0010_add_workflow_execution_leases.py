"""为RCA工作流增加执行租约和故障接管字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_0010"
down_revision: str | None = "20260628_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加租约字段、状态约束和过期任务扫描索引。"""
    op.add_column(
        "workflow_runs",
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "execution_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    # 旧版本没有消费者租约；历史运行中记录应转为可接管，而不是阻断迁移。
    op.execute(
        sa.text(
            "UPDATE workflow_runs "
            "SET lease_owner = 'migration-recovery', "
            "heartbeat_at = started_at, "
            "lease_expires_at = GREATEST("
            "started_at + INTERVAL '1 microsecond', "
            "CURRENT_TIMESTAMP - INTERVAL '1 microsecond'"
            "), "
            "execution_attempts = GREATEST(execution_attempts, 1) "
            "WHERE status = 'RUNNING'"
        )
    )
    op.create_check_constraint(
        op.f("ck_workflow_runs_non_negative_execution_attempts"),
        "workflow_runs",
        "execution_attempts >= 0",
    )
    op.create_check_constraint(
        op.f("ck_workflow_runs_valid_execution_lease"),
        "workflow_runs",
        "("
        "(status = 'RUNNING' "
        "AND lease_owner IS NOT NULL "
        "AND lease_expires_at IS NOT NULL "
        "AND heartbeat_at IS NOT NULL "
        "AND heartbeat_at >= started_at "
        "AND lease_expires_at > heartbeat_at "
        "AND execution_attempts >= 1) OR "
        "(status <> 'RUNNING' "
        "AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL "
        "AND heartbeat_at IS NULL)"
        ")",
    )
    op.create_index(
        "ix_workflow_runs_expired_lease",
        "workflow_runs",
        ["status", "lease_expires_at", "workflow_run_id"],
        unique=False,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )


def downgrade() -> None:
    """删除租约索引、约束和字段。"""
    op.drop_index(
        "ix_workflow_runs_expired_lease",
        table_name="workflow_runs",
    )
    op.drop_constraint(
        op.f("ck_workflow_runs_valid_execution_lease"),
        "workflow_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_workflow_runs_non_negative_execution_attempts"),
        "workflow_runs",
        type_="check",
    )
    op.drop_column("workflow_runs", "execution_attempts")
    op.drop_column("workflow_runs", "heartbeat_at")
    op.drop_column("workflow_runs", "lease_expires_at")
    op.drop_column("workflow_runs", "lease_owner")
