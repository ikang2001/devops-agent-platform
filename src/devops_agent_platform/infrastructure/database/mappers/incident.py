from devops_agent_platform.domain.enums import AlertSeverity, IncidentStatus
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.infrastructure.database.models.incident import (
    IncidentRecord,
)


class IncidentMapper:
    """在事故领域聚合与持久化记录之间执行显式转换。"""

    @staticmethod
    def to_record(incident: Incident) -> IncidentRecord:
        """创建携带乐观锁版本的 ORM 记录。"""
        return IncidentRecord(
            incident_id=incident.incident_id,
            tenant_id=incident.tenant_id,
            service_name=incident.service_name,
            severity=incident.severity.value,
            status=incident.status.value,
            title=incident.title,
            created_at=incident.created_at,
            updated_at=incident.updated_at,
            version=incident.version,
            resolved_by=incident.resolved_by,
            resolution_reason=incident.resolution_reason,
            resolved_at=incident.resolved_at,
            resolution_idempotency_key_hash=(
                incident.resolution_idempotency_key_hash
            ),
            resolution_request_hash=incident.resolution_request_hash,
            resolution_trace_id=incident.resolution_trace_id,
            closed_by=incident.closed_by,
            closure_reason=incident.closure_reason,
            closed_at=incident.closed_at,
            closure_idempotency_key_hash=(
                incident.closure_idempotency_key_hash
            ),
            closure_request_hash=incident.closure_request_hash,
            closure_trace_id=incident.closure_trace_id,
        )

    @staticmethod
    def to_domain(record: IncidentRecord) -> Incident:
        """恢复不依赖 SQLAlchemy 的事故领域聚合。"""
        return Incident(
            incident_id=record.incident_id,
            tenant_id=record.tenant_id,
            service_name=record.service_name,
            severity=AlertSeverity(record.severity),
            status=IncidentStatus(record.status),
            title=record.title,
            created_at=record.created_at,
            updated_at=record.updated_at,
            version=record.version,
            resolved_by=record.resolved_by,
            resolution_reason=record.resolution_reason,
            resolved_at=record.resolved_at,
            resolution_idempotency_key_hash=(
                record.resolution_idempotency_key_hash
            ),
            resolution_request_hash=record.resolution_request_hash,
            resolution_trace_id=record.resolution_trace_id,
            closed_by=record.closed_by,
            closure_reason=record.closure_reason,
            closed_at=record.closed_at,
            closure_idempotency_key_hash=(
                record.closure_idempotency_key_hash
            ),
            closure_request_hash=record.closure_request_hash,
            closure_trace_id=record.closure_trace_id,
        )
