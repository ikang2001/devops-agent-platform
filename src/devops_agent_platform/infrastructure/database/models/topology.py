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
)
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class TopologyNodeRecord(Base):
    __tablename__ = "topology_nodes"
    __table_args__ = (
        Index("ix_topology_nodes_tenant_environment", "tenant_id", "environment"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="valid_topology_node_confidence"
        ),
    )

    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    node_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    service_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)


class TopologyEdgeRecord(Base):
    __tablename__ = "topology_edges"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_node_id",
            "target_node_id",
            "relation",
            name="uq_topology_edge_key",
        ),
        Index("ix_topology_edges_tenant_source", "tenant_id", "source_node_id"),
        CheckConstraint(
            "source_node_id <> target_node_id", name="topology_edge_not_self_loop"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="valid_topology_edge_confidence"
        ),
    )

    edge_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_node_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("topology_nodes.node_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_node_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("topology_nodes.node_id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
