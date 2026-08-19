from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.domain.enums import RCAConclusionStatus
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class RCAReportRecord(Base):
    """不可变 RCA 报告数据库快照。"""

    __tablename__ = "rca_reports"
    __table_args__ = (
        CheckConstraint(
            "conclusion_status IN "
            f"({', '.join(repr(item.value) for item in RCAConclusionStatus)})",
            name="valid_rca_conclusion_status",
        ),
        CheckConstraint(
            "execution_attempt >= 1",
            name="positive_rca_report_attempt",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="valid_rca_report_confidence",
        ),
        UniqueConstraint(
            "tenant_id",
            "workflow_run_id",
            "execution_attempt",
            name="uq_rca_report_workflow_attempt",
        ),
        Index(
            "ix_rca_report_tenant_incident_generated",
            "tenant_id",
            "incident_id",
            "generated_at",
            "report_id",
        ),
    )

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    incident_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "incidents.incident_id",
            name="fk_rca_report_incident_id_incidents",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "workflow_runs.workflow_run_id",
            name="fk_rca_report_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    execution_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    conclusion_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type_counts_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    recommendations_json: Mapped[str] = mapped_column(Text, nullable=False)
    suspected_root_node: Mapped[str | None] = mapped_column(String(256), nullable=True)
    root_cause_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_cause_resource: Mapped[str | None] = mapped_column(String(256), nullable=True)
    selected_candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    root_cause_candidates_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
    )
    causal_chain_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    affected_services_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    blast_radius_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    generator_name: Mapped[str] = mapped_column(String(128), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
