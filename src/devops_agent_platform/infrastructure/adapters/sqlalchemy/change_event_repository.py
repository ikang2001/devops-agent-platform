from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import AppValidationError, ConflictError
from devops_agent_platform.domain.models.change_event import ChangeEvent
from devops_agent_platform.infrastructure.database.mappers.change_event import (
    ChangeEventMapper,
)
from devops_agent_platform.infrastructure.database.models.change_event import (
    ChangeEventRecord,
)


class SQLAlchemyChangeEventRepository:
    """基于 AsyncSession 的 Change Event 持久化和有界查询适配器。"""

    MAX_LIMIT = 100

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, change_event: ChangeEvent) -> None:
        """保存变更事实，仅 flush，不提交外层事务。"""
        record = ChangeEventMapper.to_record(change_event)
        try:
            self._session.add(record)
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"Change event persistence conflict: {change_event.change_event_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist change event") from exc

    async def get_by_id(
        self,
        change_event_id: str,
        tenant_id: str,
    ) -> ChangeEvent | None:
        """按变更 ID 和租户隔离读取。"""
        self._validate_lookup_value("change_event_id", change_event_id, 64)
        self._validate_lookup_value("tenant_id", tenant_id, 128)
        statement = (
            select(ChangeEventRecord)
            .where(
                ChangeEventRecord.change_event_id == change_event_id,
                ChangeEventRecord.tenant_id == tenant_id,
            )
            .limit(1)
        )
        return await self._load_one(statement)

    async def get_by_external_event_id(
        self,
        tenant_id: str,
        source: str,
        external_event_id: str,
    ) -> ChangeEvent | None:
        """按租户、来源和上游事件 ID 查询幂等事实。"""
        self._validate_lookup_value("tenant_id", tenant_id, 128)
        self._validate_lookup_value("source", source, 128)
        self._validate_lookup_value("external_event_id", external_event_id, 256)
        statement = (
            select(ChangeEventRecord)
            .where(
                ChangeEventRecord.tenant_id == tenant_id,
                ChangeEventRecord.source == source,
                ChangeEventRecord.external_event_id == external_event_id,
            )
            .limit(1)
        )
        return await self._load_one(statement)

    async def list_for_service(
        self,
        tenant_id: str,
        service_name: str,
        *,
        limit: int = 50,
    ) -> list[ChangeEvent]:
        """按服务倒序读取最新的有限变更记录。"""
        self._validate_lookup_value("tenant_id", tenant_id, 128)
        self._validate_lookup_value("service_name", service_name, 256)
        self._validate_limit(limit)
        statement = (
            select(ChangeEventRecord)
            .where(
                ChangeEventRecord.tenant_id == tenant_id,
                ChangeEventRecord.service_name == service_name,
            )
            .order_by(
                ChangeEventRecord.started_at.desc(),
                ChangeEventRecord.change_event_id.desc(),
            )
            .limit(limit)
        )
        return await self._load_many(statement)

    async def list_in_time_window(
        self,
        tenant_id: str,
        service_name: str,
        started_at_from: datetime,
        started_at_to: datetime,
        *,
        limit: int = 50,
    ) -> list[ChangeEvent]:
        """按服务和闭区间 ``started_at`` 读取有限变更记录。"""
        self._validate_lookup_value("tenant_id", tenant_id, 128)
        self._validate_lookup_value("service_name", service_name, 256)
        self._validate_timestamp("started_at_from", started_at_from)
        self._validate_timestamp("started_at_to", started_at_to)
        self._validate_limit(limit)
        if started_at_to < started_at_from:
            raise AppValidationError("started_at_to must not precede started_at_from")
        statement = (
            select(ChangeEventRecord)
            .where(
                ChangeEventRecord.tenant_id == tenant_id,
                ChangeEventRecord.service_name == service_name,
                ChangeEventRecord.started_at >= started_at_from,
                ChangeEventRecord.started_at <= started_at_to,
            )
            .order_by(
                ChangeEventRecord.started_at.desc(),
                ChangeEventRecord.change_event_id.desc(),
            )
            .limit(limit)
        )
        return await self._load_many(statement)

    async def _load_one(self, statement):
        try:
            record = await self._session.scalar(statement)
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load change event") from exc
        if record is None:
            return None
        try:
            return ChangeEventMapper.to_domain(record)
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError(
                "Stored change event violates the domain contract"
            ) from exc

    async def _load_many(self, statement) -> list[ChangeEvent]:
        try:
            records = (await self._session.scalars(statement)).all()
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not list change events") from exc
        try:
            return [ChangeEventMapper.to_domain(record) for record in records]
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError(
                "Stored change event violates the domain contract"
            ) from exc

    @staticmethod
    def _validate_lookup_value(field_name: str, value: str, max_length: int) -> None:
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

    @classmethod
    def _validate_limit(cls, limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise AppValidationError("limit must be an integer")
        if not 1 <= limit <= cls.MAX_LIMIT:
            raise AppValidationError(f"limit must be between 1 and {cls.MAX_LIMIT}")

    @staticmethod
    def _validate_timestamp(field_name: str, value: datetime) -> None:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(f"{field_name} must include timezone information")
