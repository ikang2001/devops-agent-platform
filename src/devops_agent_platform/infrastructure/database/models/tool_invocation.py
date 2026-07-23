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

from devops_agent_platform.domain.enums import (
    ToolInvocationStatus,
    ToolRiskLevel,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime

_TERMINAL_STATUSES = (
    ToolInvocationStatus.SUCCEEDED,
    ToolInvocationStatus.FAILED,
)


class ToolInvocationRecord(Base):
    """已完成工具调用的数据库审计记录。

    表中刻意不保存原始输入，只保存哈希和有限摘要；详细成功输出由 Evidence
    负责承载，从数据库结构上降低敏感参数被重复扩散的风险。
    """

    __tablename__ = "tool_invocations"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            f"({', '.join(repr(item.value) for item in _TERMINAL_STATUSES)})",
            name="valid_tool_invocation_terminal_status",
        ),
        CheckConstraint(
            "risk_level IN "
            f"({', '.join(repr(item.value) for item in ToolRiskLevel)})",
            name="valid_tool_invocation_risk_level",
        ),
        CheckConstraint(
            "execution_attempt >= 1",
            name="positive_tool_invocation_attempt",
        ),
        CheckConstraint(
            "latency_ms >= 0",
            name="non_negative_tool_invocation_latency",
        ),
        UniqueConstraint(
            "tenant_id",
            "workflow_run_id",
            "execution_attempt",
            "step_id",
            name="uq_tool_invocation_workflow_step",
        ),
        Index(
            "ix_tool_invocation_tenant_workflow_started",
            "tenant_id",
            "workflow_run_id",
            "started_at",
            "invocation_id",
        ),
        Index(
            "ix_tool_invocation_tenant_incident_started",
            "tenant_id",
            "incident_id",
            "started_at",
        ),
        Index(
            "ix_tool_invocation_tenant_status_started",
            "tenant_id",
            "status",
            "started_at",
        ),
    )

    invocation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    incident_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "incidents.incident_id",
            name="fk_tool_invocation_incident_id_incidents",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "workflow_runs.workflow_run_id",
            name="fk_tool_invocation_workflow_run_id_workflow_runs",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    execution_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_summary: Mapped[str] = mapped_column(String(256), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
