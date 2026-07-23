from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import OutboxStatus
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)
from devops_agent_platform.ports.outbox_metrics import OutboxBacklogSnapshot

_MONITORED_STATUSES = (
    OutboxStatus.PENDING,
    OutboxStatus.PROCESSING,
    OutboxStatus.FAILED,
)
_BACKLOG_PREDICATE = (
    "status IN ('PENDING', 'PROCESSING', 'FAILED')"
)


class SQLAlchemyOutboxMetricsReader:
    """使用单条只读聚合SQL读取Outbox积压快照。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def load_snapshot(self) -> OutboxBacklogSnapshot:
        """按状态统计数量，并计算最早未发布事件时间。"""
        statement = (
            select(
                OutboxEventRecord.status,
                func.count().label("event_count"),
                func.min(OutboxEventRecord.created_at).label("oldest_created_at"),
            )
            .where(text(_BACKLOG_PREDICATE))
            .group_by(OutboxEventRecord.status)
        )
        try:
            async with self._session_factory() as session:
                rows = (await session.execute(statement)).all()
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not read outbox backlog metrics"
            ) from exc

        counts = {status: 0 for status in _MONITORED_STATUSES}
        oldest_unpublished_at = None
        for status_value, event_count, oldest_created_at in rows:
            status = OutboxStatus(status_value)
            counts[status] = int(event_count)
            if (
                status in {OutboxStatus.PENDING, OutboxStatus.PROCESSING}
                and oldest_created_at is not None
                and (
                    oldest_unpublished_at is None
                    or oldest_created_at < oldest_unpublished_at
                )
            ):
                oldest_unpublished_at = oldest_created_at

        return OutboxBacklogSnapshot(
            counts=tuple(
                (status, counts[status])
                for status in _MONITORED_STATUSES
            ),
            oldest_unpublished_at=oldest_unpublished_at,
        )
