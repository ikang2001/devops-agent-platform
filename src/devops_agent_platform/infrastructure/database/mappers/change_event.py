from devops_agent_platform.domain.enums import ChangeEventStatus, ChangeType
from devops_agent_platform.domain.models.change_event import ChangeEvent
from devops_agent_platform.infrastructure.database.models.change_event import (
    ChangeEventRecord,
)


class ChangeEventMapper:
    """在 ChangeEvent 领域对象与 ORM 记录之间执行显式转换。"""

    @staticmethod
    def to_record(change_event: ChangeEvent) -> ChangeEventRecord:
        """把已通过领域校验的变更事件转换成数据库记录。"""
        return ChangeEventRecord(
            change_event_id=change_event.change_event_id,
            tenant_id=change_event.tenant_id,
            source=change_event.source,
            external_event_id=change_event.external_event_id,
            service_name=change_event.service_name,
            resource_type=change_event.resource_type,
            resource_id=change_event.resource_id,
            change_type=change_event.change_type.value,
            status=change_event.status.value,
            version_before=change_event.version_before,
            version_after=change_event.version_after,
            operator_id=change_event.operator_id,
            summary=change_event.summary,
            metadata_json=change_event.metadata_json,
            started_at=change_event.started_at,
            completed_at=change_event.completed_at,
            created_at=change_event.created_at,
            request_hash=change_event.request_hash,
        )

    @staticmethod
    def to_domain(record: ChangeEventRecord) -> ChangeEvent:
        """从数据库记录恢复不依赖 ORM 生命周期的领域对象。"""
        return ChangeEvent(
            change_event_id=record.change_event_id,
            tenant_id=record.tenant_id,
            source=record.source,
            external_event_id=record.external_event_id,
            service_name=record.service_name,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
            change_type=ChangeType(record.change_type),
            status=ChangeEventStatus(record.status),
            version_before=record.version_before,
            version_after=record.version_after,
            operator_id=record.operator_id,
            summary=record.summary,
            metadata_json=record.metadata_json,
            started_at=record.started_at,
            completed_at=record.completed_at,
            created_at=record.created_at,
            request_hash=record.request_hash,
        )
