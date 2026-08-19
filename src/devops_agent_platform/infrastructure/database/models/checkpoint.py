from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class InvestigationCheckpointRecord(Base):
    """跨 Worker 保存有界动态调查的最后一致状态。"""

    __tablename__ = "investigation_checkpoints"
    __table_args__ = (
        Index(
            "ix_investigation_checkpoints_tenant_updated",
            "tenant_id",
            "updated_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(length=64), primary_key=True)
    checkpoint_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
