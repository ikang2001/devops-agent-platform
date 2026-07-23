from devops_agent_platform.domain.enums import EvidenceType
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.infrastructure.database.models.evidence import (
    EvidenceRecord,
)


class EvidenceMapper:
    """在 Evidence 领域对象与数据库记录之间做显式转换。"""

    @staticmethod
    def to_record(evidence: Evidence) -> EvidenceRecord:
        """把领域证据转换为待持久化 ORM 记录。"""
        return EvidenceRecord(
            evidence_id=evidence.evidence_id,
            tenant_id=evidence.tenant_id,
            incident_id=evidence.incident_id,
            workflow_run_id=evidence.workflow_run_id,
            execution_attempt=evidence.execution_attempt,
            step_id=evidence.step_id,
            tool_name=evidence.tool_name,
            tool_version=evidence.tool_version,
            evidence_type=evidence.evidence_type.value,
            source=evidence.source,
            summary=evidence.summary,
            content_json=evidence.content_json,
            content_sha256=evidence.content_sha256,
            confidence=evidence.confidence,
            collected_at=evidence.collected_at,
        )

    @staticmethod
    def to_domain(record: EvidenceRecord) -> Evidence:
        """把 ORM 记录恢复为不依赖 SQLAlchemy 的领域证据。"""
        return Evidence(
            evidence_id=record.evidence_id,
            tenant_id=record.tenant_id,
            incident_id=record.incident_id,
            workflow_run_id=record.workflow_run_id,
            execution_attempt=record.execution_attempt,
            step_id=record.step_id,
            tool_name=record.tool_name,
            tool_version=record.tool_version,
            evidence_type=EvidenceType(record.evidence_type),
            source=record.source,
            summary=record.summary,
            content_json=record.content_json,
            content_sha256=record.content_sha256,
            confidence=record.confidence,
            collected_at=record.collected_at,
        )
