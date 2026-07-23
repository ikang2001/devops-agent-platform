from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class ToolPermissionGrantRecord(Base):
    """租户内操作者的工具权限授权主体记录。"""

    __tablename__ = "tool_permission_grants"
    __table_args__ = (
        CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name="non_empty_tenant_id",
        ),
        CheckConstraint(
            "length(trim(operator_id)) > 0",
            name="non_empty_operator_id",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="valid_updated_at",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="valid_expires_at",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="valid_revoked_at",
        ),
        CheckConstraint(
            "version >= 1",
            name="positive_version",
        ),
        Index(
            "uq_tool_permission_grants_active_operator",
            "tenant_id",
            "operator_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_tool_permission_grants_operator_history",
            "tenant_id",
            "operator_id",
            "created_at",
            "grant_id",
        ),
    )

    grant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
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


class ToolPermissionTagRecord(Base):
    """授权主体拥有的单个规范化权限标签。"""

    __tablename__ = "tool_permission_tags"
    __table_args__ = (
        CheckConstraint(
            "length(trim(permission_tag)) > 0",
            name="non_empty_permission_tag",
        ),
        Index(
            "ix_tool_permission_tags_permission_tag",
            "permission_tag",
        ),
    )

    grant_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "tool_permission_grants.grant_id",
            name=(
                "fk_tool_permission_tags_grant_id_"
                "tool_permission_grants"
            ),
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    permission_tag: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )


class ToolPermissionOperationRecord(Base):
    """权限管理幂等结果与审计索引记录。"""

    __tablename__ = "tool_permission_operations"
    __table_args__ = (
        CheckConstraint(
            "action IN ('SET', 'REVOKE')",
            name="valid_action",
        ),
        CheckConstraint(
            "result_version >= 1",
            name="positive_result_version",
        ),
        Index(
            "uq_tool_permission_operations_tenant_idempotency",
            "tenant_id",
            "idempotency_key_hash",
            unique=True,
        ),
        Index(
            "ix_tool_permission_operations_subject_history",
            "tenant_id",
            "operator_id",
            "occurred_at",
            "operation_id",
        ),
    )

    operation_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    result_grant_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "tool_permission_grants.grant_id",
            name="fk_permission_ops_grant_permission_grants",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    result_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    requested_by: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )
