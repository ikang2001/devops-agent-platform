from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class AlertRecord(Base):
    """告警事实的数据库记录。

    ORM 模型只描述持久化结构，不承载告警聚合和事故创建规则。领域规则继续保留在
    domain/application 层，防止业务逻辑被 SQLAlchemy 生命周期绑架。
    """

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'CRITICAL')",
            name="valid_severity",
        ),
        UniqueConstraint(
            "tenant_id",
            "source",
            "external_event_id",
            name="uq_alerts_tenant_source_external_event",
        ),
        Index("ix_alerts_tenant_fingerprint", "tenant_id", "fingerprint"),
        Index("ix_alerts_tenant_starts_at", "tenant_id", "starts_at"),
        Index("ix_alerts_tenant_incident", "tenant_id", "incident_id"),
    )

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    service_name: Mapped[str] = mapped_column(String(256), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )
    fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    incident_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "incidents.incident_id",
            name="fk_alerts_incident_id_incidents",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    environment: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default", server_default="default"
    )
    alert_type: Mapped[str] = mapped_column(
        String(128), nullable=False, default="generic", server_default="generic"
    )
    labels_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )
