from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.domain.enums import TicketSubmissionStatus
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class TicketSubmissionRecord(Base):
    """外部工单提交请求的本地事务记录。"""

    __tablename__ = "ticket_submissions"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            f"({', '.join(repr(item.value) for item in TicketSubmissionStatus)})",
            name="valid_ticket_submission_status",
        ),
        CheckConstraint(
            "version IN (1, 2)",
            name="valid_ticket_submission_version",
        ),
        CheckConstraint(
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
            name="valid_ticket_submission_result_state",
        ),
        CheckConstraint(
            "length(trim(target_system)) > 0",
            name="non_empty_ticket_submission_target",
        ),
        UniqueConstraint(
            "tenant_id",
            "ticket_draft_id",
            "target_system",
            name="uq_ticket_submissions_tenant_draft_target",
        ),
        Index(
            "uq_ticket_submissions_tenant_idempotency",
            "tenant_id",
            "idempotency_key_hash",
            unique=True,
        ),
        Index(
            "uq_ticket_submissions_tenant_result_idempotency",
            "tenant_id",
            "result_idempotency_key_hash",
            unique=True,
        ),
        Index(
            "uq_ticket_submissions_external_ticket",
            "tenant_id",
            "target_system",
            "external_ticket_id",
            unique=True,
            postgresql_where=text("external_ticket_id IS NOT NULL"),
            sqlite_where=text("external_ticket_id IS NOT NULL"),
        ),
        Index(
            "ix_ticket_submissions_tenant_workflow_status",
            "tenant_id",
            "workflow_run_id",
            "status",
            "requested_at",
            "ticket_submission_id",
        ),
    )

    ticket_submission_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ticket_draft_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "ticket_drafts.ticket_draft_id",
            name="fk_ticket_submissions_ticket_draft_id_ticket_drafts",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "workflow_runs.workflow_run_id",
            name="fk_ticket_submissions_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    target_system: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    external_ticket_id: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )
    external_ticket_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    completed_by: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    result_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    result_request_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    result_trace_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
