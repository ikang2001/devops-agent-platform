from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class TicketDraftRecord(Base):
    """RCA 工作流派生的本地工单草稿记录。"""

    __tablename__ = "ticket_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'REJECTED')",
            name="valid_ticket_draft_status",
        ),
        CheckConstraint(
            "priority IN ('P1', 'P2', 'P3')",
            name="valid_ticket_priority",
        ),
        CheckConstraint(
            "version IN (1, 2)",
            name="valid_ticket_draft_version",
        ),
        CheckConstraint(
            "(status = 'DRAFT' AND version = 1 "
            "AND decided_by IS NULL AND decision_reason IS NULL "
            "AND decided_at IS NULL "
            "AND decision_idempotency_key_hash IS NULL "
            "AND decision_request_hash IS NULL "
            "AND decision_trace_id IS NULL) OR "
            "(status = 'APPROVED' AND version = 2 "
            "AND decided_by IS NOT NULL AND decision_reason IS NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_idempotency_key_hash IS NOT NULL "
            "AND decision_request_hash IS NOT NULL "
            "AND decision_trace_id IS NOT NULL) OR "
            "(status = 'REJECTED' AND version = 2 "
            "AND decided_by IS NOT NULL AND decision_reason IS NOT NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_idempotency_key_hash IS NOT NULL "
            "AND decision_request_hash IS NOT NULL "
            "AND decision_trace_id IS NOT NULL)",
            name="valid_ticket_decision_state",
        ),
        UniqueConstraint(
            "tenant_id",
            "workflow_run_id",
            name="uq_ticket_drafts_tenant_workflow",
        ),
        Index(
            "uq_ticket_drafts_tenant_idempotency",
            "tenant_id",
            "idempotency_key_hash",
            unique=True,
        ),
        Index(
            "ix_ticket_drafts_tenant_incident_created",
            "tenant_id",
            "incident_id",
            "created_at",
            "ticket_draft_id",
        ),
        Index(
            "uq_ticket_drafts_tenant_decision_idempotency",
            "tenant_id",
            "decision_idempotency_key_hash",
            unique=True,
        ),
    )

    ticket_draft_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    incident_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "incidents.incident_id",
            name="fk_ticket_drafts_incident_id_incidents",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "workflow_runs.workflow_run_id",
            name="fk_ticket_drafts_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    report_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "rca_reports.report_id",
            name="fk_ticket_drafts_report_id_rca_reports",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    recommendations_json: Mapped[str] = mapped_column(Text, nullable=False)
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
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    decided_by: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    decision_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    decision_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    decision_request_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    decision_trace_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
