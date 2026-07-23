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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.domain.enums import RunbookStatus
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class RunbookRecord(Base):
    """租户内版本化 Runbook 数据库快照。"""

    __tablename__ = "runbooks"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            f"({', '.join(repr(item.value) for item in RunbookStatus)})",
            name="valid_runbook_status",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 1000",
            name="valid_runbook_priority",
        ),
        CheckConstraint(
            "revision >= 1",
            name="positive_runbook_revision",
        ),
        CheckConstraint(
            "(status = 'DRAFT' AND published_at IS NULL) OR "
            "(status IN ('PUBLISHED', 'ARCHIVED') "
            "AND published_at IS NOT NULL)",
            name="valid_runbook_published_at",
        ),
        CheckConstraint(
            "published_at IS NULL OR published_at <= updated_at",
            name="valid_runbook_timeline",
        ),
        CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name="non_empty_runbook_tenant",
        ),
        CheckConstraint(
            "length(trim(service_name)) > 0",
            name="non_empty_runbook_service",
        ),
        UniqueConstraint(
            "tenant_id",
            "runbook_key",
            "version",
            name="uq_runbooks_tenant_key_version",
        ),
        Index(
            "uq_runbooks_tenant_published_key",
            "tenant_id",
            "runbook_key",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
            sqlite_where=text("status = 'PUBLISHED'"),
        ),
        Index(
            "ix_runbooks_tenant_status_service_priority",
            "tenant_id",
            "status",
            "service_name",
            "priority",
            "updated_at",
            "runbook_id",
        ),
    )

    runbook_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    runbook_key: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    service_name: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    steps_json: Mapped[str] = mapped_column(Text, nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )


class RunbookOperationRecord(Base):
    """Runbook 管理请求的幂等结果和审计索引。"""

    __tablename__ = "runbook_operations"
    __table_args__ = (
        CheckConstraint(
            "action IN ('SAVE_DRAFT', 'PUBLISH')",
            name="valid_runbook_operation_action",
        ),
        CheckConstraint(
            "result_revision >= 1",
            name="positive_runbook_result_revision",
        ),
        Index(
            "uq_runbook_operations_tenant_idempotency",
            "tenant_id",
            "idempotency_key_hash",
            unique=True,
        ),
        Index(
            "ix_runbook_operations_key_history",
            "tenant_id",
            "runbook_key",
            "occurred_at",
            "operation_id",
        ),
    )

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    runbook_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    result_runbook_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "runbooks.runbook_id",
            name="fk_runbook_operations_result_runbook_runbooks",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    result_status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )


class RunbookHeadRecord(Base):
    """Runbook 逻辑聚合的稳定并发锁和当前版本指针。"""

    __tablename__ = "runbook_heads"
    __table_args__ = (
        CheckConstraint(
            "revision >= 0",
            name="non_negative_runbook_head_revision",
        ),
        CheckConstraint(
            "draft_runbook_id IS NULL OR published_runbook_id IS NULL "
            "OR draft_runbook_id <> published_runbook_id",
            name="distinct_runbook_head_pointers",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )
    runbook_key: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    draft_runbook_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "runbooks.runbook_id",
            name="fk_runbook_heads_draft_runbook_runbooks",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    published_runbook_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "runbooks.runbook_id",
            name="fk_runbook_heads_published_runbook_runbooks",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
