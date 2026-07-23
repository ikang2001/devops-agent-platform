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

from devops_agent_platform.domain.enums import EvidenceType
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class EvidenceRecord(Base):
    """RCA 结构化证据的数据库记录。

    该表保存有限大小的结构化证据摘要，不保存无限长原始日志。原始日志、Trace
    明细仍应由 Loki/Tempo 等系统承载，Evidence 只负责审计、重放和报告引用。
    """

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "evidence_type IN "
            f"({', '.join(repr(item.value) for item in EvidenceType)})",
            name="valid_evidence_type",
        ),
        CheckConstraint(
            "execution_attempt >= 1",
            name="positive_execution_attempt",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="valid_confidence",
        ),
        UniqueConstraint(
            "tenant_id",
            "workflow_run_id",
            "execution_attempt",
            "step_id",
            "evidence_type",
            "source",
            name="uq_evidence_workflow_step_source",
        ),
        Index(
            "ix_evidence_tenant_incident_collected",
            "tenant_id",
            "incident_id",
            "collected_at",
            "evidence_id",
        ),
        Index(
            "ix_evidence_tenant_workflow_step",
            "tenant_id",
            "workflow_run_id",
            "execution_attempt",
            "step_id",
        ),
        Index(
            "ix_evidence_tenant_type_collected",
            "tenant_id",
            "evidence_type",
            "collected_at",
        ),
    )

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    incident_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "incidents.incident_id",
            name="fk_evidence_incident_id_incidents",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "workflow_runs.workflow_run_id",
            name="fk_evidence_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    execution_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
