from devops_agent_platform.application.events import (
    ClaimedOutboxEvent,
    OutboxEvent,
)
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)


class OutboxEventMapper:
    """把应用事件转换为待发布的 Outbox 记录。"""

    @staticmethod
    def to_record(event: OutboxEvent) -> OutboxEventRecord:
        """创建默认状态为 PENDING、重试次数为零的数据库记录。"""
        return OutboxEventRecord(
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            event_type=event.event_type,
            schema_version=event.schema_version,
            payload=event.payload,
            occurred_at=event.occurred_at,
            trace_id=event.trace_id,
        )

    @staticmethod
    def to_claimed(record: OutboxEventRecord) -> ClaimedOutboxEvent:
        """把已抢占数据库记录复制为脱离 Session 的事件快照。"""
        event = OutboxEvent(
            event_id=record.event_id,
            tenant_id=record.tenant_id,
            aggregate_type=record.aggregate_type,
            aggregate_id=record.aggregate_id,
            event_type=record.event_type,
            schema_version=record.schema_version,
            payload=record.payload,
            occurred_at=record.occurred_at,
            trace_id=record.trace_id,
        )
        return ClaimedOutboxEvent(event=event, attempts=record.attempts)
