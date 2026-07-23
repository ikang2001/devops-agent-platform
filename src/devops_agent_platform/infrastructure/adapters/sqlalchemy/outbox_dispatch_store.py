from datetime import datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from devops_agent_platform.application.events import ClaimedOutboxEvent
from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import OutboxStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.infrastructure.database.mappers.outbox import (
    OutboxEventMapper,
)
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)


class SQLAlchemyOutboxDispatchStore:
    """使用独立短事务抢占和完成 Outbox 投递状态。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def claim_batch(
        self,
        worker_id: str,
        now: datetime,
        locked_until: datetime,
        limit: int,
        max_attempts: int,
    ) -> list[ClaimedOutboxEvent]:
        """使用 SKIP LOCKED 抢占到期事件并提交租约。"""
        worker_id = validate_worker_id(worker_id)
        self._validate_timestamp("now", now)
        self._validate_timestamp("locked_until", locked_until)
        if locked_until <= now:
            raise AppValidationError("locked_until must be later than now")
        self._validate_positive_int("limit", limit, 100)
        self._validate_positive_int("max_attempts", max_attempts, 100)

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await self._mark_exhausted_stale_events(
                        session,
                        now,
                        max_attempts,
                    )
                    records = (
                        await session.scalars(
                            self._build_claim_statement(
                                now=now,
                                limit=limit,
                                max_attempts=max_attempts,
                            )
                        )
                    ).all()
                    claimed: list[ClaimedOutboxEvent] = []
                    for record in records:
                        record.status = OutboxStatus.PROCESSING.value
                        record.attempts += 1
                        record.lock_id = worker_id
                        record.locked_until = locked_until
                        claimed.append(OutboxEventMapper.to_claimed(record))
                return claimed
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not claim outbox events") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored outbox event violates the event contract"
            ) from exc

    async def mark_published(
        self,
        event_id: str,
        worker_id: str,
        published_at: datetime,
    ) -> None:
        """仅允许当前租约持有者把事件标记为已发布。"""
        self._validate_text("event_id", event_id, 64)
        worker_id = validate_worker_id(worker_id)
        self._validate_timestamp("published_at", published_at)
        await self._update_owned_event(
            event_id=event_id,
            worker_id=worker_id,
            values={
                "status": OutboxStatus.PUBLISHED.value,
                "published_at": published_at,
                "lock_id": None,
                "locked_until": None,
                "last_error": None,
            },
        )

    async def mark_retry(
        self,
        event_id: str,
        worker_id: str,
        available_at: datetime,
        last_error: str,
    ) -> None:
        """发布失败后释放租约并按退避时间重新排队。"""
        self._validate_text("event_id", event_id, 64)
        worker_id = validate_worker_id(worker_id)
        self._validate_timestamp("available_at", available_at)
        self._validate_error(last_error)
        await self._update_owned_event(
            event_id=event_id,
            worker_id=worker_id,
            values={
                "status": OutboxStatus.PENDING.value,
                "available_at": available_at,
                "lock_id": None,
                "locked_until": None,
                "last_error": last_error,
            },
        )

    async def mark_failed(
        self,
        event_id: str,
        worker_id: str,
        last_error: str,
    ) -> None:
        """把达到最大尝试次数的事件标记为终态失败。"""
        self._validate_text("event_id", event_id, 64)
        worker_id = validate_worker_id(worker_id)
        self._validate_error(last_error)
        await self._update_owned_event(
            event_id=event_id,
            worker_id=worker_id,
            values={
                "status": OutboxStatus.FAILED.value,
                "lock_id": None,
                "locked_until": None,
                "last_error": last_error,
            },
        )

    @staticmethod
    def _build_claim_statement(
        now: datetime,
        limit: int,
        max_attempts: int,
    ) -> Select[tuple[OutboxEventRecord]]:
        """构造可由多个Worker并发执行的抢占语句。"""
        pending_due = and_(
            OutboxEventRecord.status == OutboxStatus.PENDING.value,
            OutboxEventRecord.available_at <= now,
        )
        stale_processing = and_(
            OutboxEventRecord.status == OutboxStatus.PROCESSING.value,
            OutboxEventRecord.locked_until.is_not(None),
            OutboxEventRecord.locked_until <= now,
        )
        return (
            select(OutboxEventRecord)
            .where(
                or_(pending_due, stale_processing),
                OutboxEventRecord.attempts < max_attempts,
            )
            .order_by(
                OutboxEventRecord.available_at,
                OutboxEventRecord.created_at,
                OutboxEventRecord.event_id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    @staticmethod
    async def _mark_exhausted_stale_events(
        session: AsyncSession,
        now: datetime,
        max_attempts: int,
    ) -> None:
        """清理达到最大次数且租约已过期的处理中事件。"""
        await session.execute(
            update(OutboxEventRecord)
            .where(
                OutboxEventRecord.status == OutboxStatus.PROCESSING.value,
                OutboxEventRecord.locked_until.is_not(None),
                OutboxEventRecord.locked_until <= now,
                OutboxEventRecord.attempts >= max_attempts,
            )
            .values(
                status=OutboxStatus.FAILED.value,
                lock_id=None,
                locked_until=None,
                last_error="Worker lease expired after maximum attempts",
            )
        )

    async def _update_owned_event(
        self,
        event_id: str,
        worker_id: str,
        values: dict[str, object],
    ) -> None:
        """使用租约所有权条件完成状态更新，拒绝旧Worker覆盖新状态。"""
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    result = await session.execute(
                        update(OutboxEventRecord)
                        .where(
                            OutboxEventRecord.event_id == event_id,
                            OutboxEventRecord.status == OutboxStatus.PROCESSING.value,
                            OutboxEventRecord.lock_id == worker_id,
                        )
                        .values(**values)
                    )
                    if result.rowcount != 1:
                        raise ConflictError(f"Outbox lease ownership lost: {event_id}")
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not update outbox dispatch state") from exc

    @staticmethod
    def _validate_text(field_name: str, value: str, max_length: int) -> None:
        """校验Worker、事件等索引字段。"""
        if not isinstance(value, str):
            raise AppValidationError(f"{field_name} must be a string")
        if not 1 <= len(value) <= max_length:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {max_length}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )

    @staticmethod
    def _validate_timestamp(field_name: str, value: datetime) -> None:
        """要求状态流转时间携带时区。"""
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(f"{field_name} must include timezone information")

    @staticmethod
    def _validate_positive_int(
        field_name: str,
        value: int,
        maximum: int,
    ) -> None:
        """校验批量和尝试次数上限。"""
        if isinstance(value, bool) or not isinstance(value, int):
            raise AppValidationError(f"{field_name} must be an integer")
        if not 1 <= value <= maximum:
            raise AppValidationError(f"{field_name} must be between 1 and {maximum}")

    @staticmethod
    def _validate_error(last_error: str) -> None:
        """限制错误摘要长度并保持单行，防止异常文本污染数据库行。"""
        if not isinstance(last_error, str) or not 1 <= len(last_error) <= 2048:
            raise AppValidationError("last_error length must be between 1 and 2048")
        if any(
            ord(character) < 32 or ord(character) == 127 for character in last_error
        ):
            raise AppValidationError("last_error must not contain control characters")
