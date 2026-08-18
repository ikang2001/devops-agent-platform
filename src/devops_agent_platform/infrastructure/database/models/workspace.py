from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_workspace_tenant_name"),
        Index("ix_workspaces_tenant_updated", "tenant_id", "updated_at"),
    )

    workspace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    prometheus_target: Mapped[str] = mapped_column(String(512), nullable=False)
    loki_target: Mapped[str] = mapped_column(String(512), nullable=False)
    tempo_target: Mapped[str] = mapped_column(String(512), nullable=False)
    knowledge_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    investigation_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    allowed_tools_json: Mapped[str] = mapped_column(Text, nullable=False)
    llm_provider_policy: Mapped[str] = mapped_column(String(128), nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WorkspaceOperationRecord(Base):
    __tablename__ = "workspace_operations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key_hash",
            name="uq_workspace_operation_idempotency",
        ),
        Index(
            "ix_workspace_operations_target",
            "tenant_id",
            "workspace_id",
            "occurred_at",
        ),
    )

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
