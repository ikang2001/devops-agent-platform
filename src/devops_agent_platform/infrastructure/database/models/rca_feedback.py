from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class RCAFeedbackRecord(Base):
    """不可变 RCA 人工反馈数据库记录。"""

    __tablename__ = "rca_feedback"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('ACCEPTED', 'PARTIAL', 'REJECTED')",
            name="valid_rca_feedback_verdict",
        ),
        Index(
            "uq_rca_feedback_tenant_idempotency",
            "tenant_id",
            "idempotency_key_hash",
            unique=True,
        ),
        Index(
            "ix_rca_feedback_tenant_workflow_created",
            "tenant_id",
            "workflow_run_id",
            "created_at",
            "feedback_id",
        ),
    )

    feedback_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "workflow_runs.workflow_run_id",
            name="fk_rca_feedback_workflow_run_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    report_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "rca_reports.report_id",
            name="fk_rca_feedback_report_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    corrected_root_cause: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    missing_evidence_types_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    unsafe_recommendation_indexes_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    follow_up_label: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
