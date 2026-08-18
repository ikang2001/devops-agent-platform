from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.types import UTCDateTime


class KnowledgeDocumentRecord(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index(
            "ix_knowledge_documents_tenant_status_service",
            "tenant_id",
            "review_status",
            "service_name",
        ),
        Index(
            "ix_knowledge_documents_tenant_fingerprint",
            "tenant_id",
            "error_fingerprint",
        ),
    )

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    service_name: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_incident_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_fingerprint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeChunkRecord(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_knowledge_chunk_ordinal"),
    )

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
