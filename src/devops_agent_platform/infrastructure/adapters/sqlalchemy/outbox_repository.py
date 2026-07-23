from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.infrastructure.database.mappers.outbox import (
    OutboxEventMapper,
)


class SQLAlchemyOutboxRepository:
    """使用当前 AsyncSession 写入事务型 Outbox。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: OutboxEvent) -> None:
        """写入待发布事件但不提交事务。"""
        record = OutboxEventMapper.to_record(event)
        try:
            self._session.add(record)
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"Outbox event persistence conflict: {event.event_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist outbox event") from exc
