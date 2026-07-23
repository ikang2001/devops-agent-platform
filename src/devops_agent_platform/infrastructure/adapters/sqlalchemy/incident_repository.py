from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import IncidentStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)
from devops_agent_platform.infrastructure.database.models.incident import (
    IncidentRecord,
)


class SQLAlchemyIncidentRepository:
    """基于 AsyncSession 的事故聚合仓储。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, incident: Incident) -> None:
        """新增或更新事故，并保护乐观锁与不可变字段。

        当前端口使用统一 ``save`` 语义，因此先按主键判断新增或更新。更新时只
        复制允许变化的字段，避免 ``merge`` 意外修改租户和创建时间。若查询开销
        成为瓶颈，应把端口拆成 ``add`` 与 ``update``，而不是牺牲数据边界。
        """
        try:
            record = await self._session.get(
                IncidentRecord,
                incident.incident_id,
            )
            if record is None:
                record = IncidentMapper.to_record(incident)
                self._session.add(record)
            else:
                self._validate_update_contract(record, incident)
                self._apply_mutable_fields(record, incident)

            await self._session.flush()
        except StaleDataError as exc:
            raise ConflictError(
                f"Incident version conflict: {incident.incident_id}"
            ) from exc
        except IntegrityError as exc:
            raise ConflictError(
                f"Incident persistence conflict: {incident.incident_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist incident") from exc

        incident.version = record.version

    async def get_by_id(
        self,
        incident_id: str,
        tenant_id: str,
    ) -> Incident | None:
        """按事故 ID 和租户 ID 精确加载聚合。

        双条件查询保证即使调用方猜到其他租户的事故 ID，也无法读取其内容。
        SQLAlchemy 使用绑定参数生成 SQL，不拼接用户输入，从入口规避 SQL 注入。
        """
        self._validate_lookup_value("incident_id", incident_id, 64)
        self._validate_lookup_value("tenant_id", tenant_id, 128)

        statement = (
            select(IncidentRecord)
            .where(
                IncidentRecord.incident_id == incident_id,
                IncidentRecord.tenant_id == tenant_id,
            )
            .limit(1)
        )
        try:
            record = await self._session.scalar(statement)
            return (
                IncidentMapper.to_domain(record)
                if record is not None
                else None
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load incident") from exc
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError(
                "Stored incident violates the domain contract"
            ) from exc

    async def list_page(
        self,
        tenant_id: str,
        statuses: frozenset[IncidentStatus],
        *,
        before_updated_at: datetime | None,
        before_incident_id: str | None,
        limit: int,
    ) -> list[Incident]:
        """按稳定倒序 Keyset 读取租户内有限事故列表。"""
        self._validate_lookup_value("tenant_id", tenant_id, 128)
        self._validate_candidate_statuses(statuses)
        if (before_updated_at is None) != (before_incident_id is None):
            raise AppValidationError(
                "incident cursor fields must be provided together"
            )
        if before_updated_at is not None:
            self._validate_aware_timestamp(
                "before_updated_at",
                before_updated_at,
            )
            assert before_incident_id is not None
            self._validate_lookup_value(
                "before_incident_id",
                before_incident_id,
                64,
            )
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 101
        ):
            raise AppValidationError("limit must be between 1 and 101")

        status_values = tuple(sorted(status.value for status in statuses))
        statement = select(IncidentRecord).where(
            IncidentRecord.tenant_id == tenant_id,
            IncidentRecord.status.in_(status_values),
        )
        if before_updated_at is not None:
            statement = statement.where(
                or_(
                    IncidentRecord.updated_at < before_updated_at,
                    and_(
                        IncidentRecord.updated_at == before_updated_at,
                        IncidentRecord.incident_id < before_incident_id,
                    ),
                )
            )
        statement = statement.order_by(
            IncidentRecord.updated_at.desc(),
            IncidentRecord.incident_id.desc(),
        ).limit(limit)
        try:
            records = (await self._session.scalars(statement)).all()
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not list incidents") from exc
        try:
            return [IncidentMapper.to_domain(record) for record in records]
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError(
                "Stored incident violates the domain contract"
            ) from exc

    async def find_candidates(
        self,
        tenant_id: str,
        service_name: str,
        statuses: frozenset[IncidentStatus],
        created_before: datetime,
        updated_after: datetime,
        limit: int = 50,
    ) -> list[Incident]:
        """加载事故创建策略需要的有界候选集合。

        ``created_before`` 对应告警时间加时钟偏差，``updated_after`` 对应告警
        时间减关联窗口。结果按更新时间和事故 ID 倒序，保证 limit 截断后仍保留
        全局最优候选，并在相同时间下保持稳定顺序。
        """
        self._validate_lookup_value("tenant_id", tenant_id, 128)
        self._validate_lookup_value("service_name", service_name, 256)
        self._validate_candidate_statuses(statuses)
        self._validate_aware_timestamp("created_before", created_before)
        self._validate_aware_timestamp("updated_after", updated_after)
        self._validate_candidate_limit(limit)
        if updated_after > created_before:
            raise AppValidationError(
                "updated_after must not be later than created_before"
            )

        status_values = tuple(sorted(status.value for status in statuses))
        statement = (
            select(IncidentRecord)
            .where(
                IncidentRecord.tenant_id == tenant_id,
                IncidentRecord.service_name == service_name,
                IncidentRecord.status.in_(status_values),
                IncidentRecord.created_at <= created_before,
                IncidentRecord.updated_at >= updated_after,
            )
            .order_by(
                IncidentRecord.updated_at.desc(),
                IncidentRecord.incident_id.desc(),
            )
            .limit(limit)
        )
        try:
            records = (await self._session.scalars(statement)).all()
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not load incident candidates"
            ) from exc
        try:
            return [IncidentMapper.to_domain(record) for record in records]
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError(
                "Stored incident violates the domain contract"
            ) from exc

    @staticmethod
    def _validate_lookup_value(
        field_name: str,
        value: str,
        max_length: int,
    ) -> None:
        """校验查询键，避免无意义查询和日志字段污染。"""
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

    @staticmethod
    def _validate_candidate_statuses(
        statuses: frozenset[IncidentStatus],
    ) -> None:
        """校验候选状态集合，避免生成空 IN 条件或接收任意字符串。"""
        if not isinstance(statuses, frozenset):
            raise AppValidationError("statuses must be a frozenset")
        if not statuses:
            raise AppValidationError("statuses must not be empty")
        if not all(isinstance(status, IncidentStatus) for status in statuses):
            raise AppValidationError(
                "statuses must contain only IncidentStatus values"
            )

    @staticmethod
    def _validate_aware_timestamp(field_name: str, value: datetime) -> None:
        """要求查询时间携带时区，避免数据库会话时区改变边界。"""
        if not isinstance(value, datetime):
            raise AppValidationError(f"{field_name} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise AppValidationError(
                f"{field_name} must include timezone information"
            )

    @staticmethod
    def _validate_candidate_limit(limit: int) -> None:
        """限制候选数量，防止配置错误引发大结果集。"""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise AppValidationError("limit must be an integer")
        if not 1 <= limit <= 100:
            raise AppValidationError("limit must be between 1 and 100")

    @staticmethod
    def _validate_update_contract(
        record: IncidentRecord,
        incident: Incident,
    ) -> None:
        """校验乐观锁版本和创建后不可修改的事故身份字段。"""
        if incident.version != record.version:
            raise ConflictError(
                f"Incident version conflict: {incident.incident_id}"
            )

        immutable_values = (
            ("tenant_id", record.tenant_id, incident.tenant_id),
            ("service_name", record.service_name, incident.service_name),
            ("created_at", record.created_at, incident.created_at),
        )
        changed_fields = [
            field_name
            for field_name, stored_value, incoming_value in immutable_values
            if stored_value != incoming_value
        ]
        if changed_fields:
            fields = ", ".join(changed_fields)
            raise ConflictError(f"Incident immutable fields changed: {fields}")

    @staticmethod
    def _apply_mutable_fields(
        record: IncidentRecord,
        incident: Incident,
    ) -> None:
        """只复制事故生命周期允许变化的持久化字段。"""
        record.severity = incident.severity.value
        record.status = incident.status.value
        record.title = incident.title
        record.updated_at = incident.updated_at
        record.resolved_by = incident.resolved_by
        record.resolution_reason = incident.resolution_reason
        record.resolved_at = incident.resolved_at
        record.resolution_idempotency_key_hash = (
            incident.resolution_idempotency_key_hash
        )
        record.resolution_request_hash = incident.resolution_request_hash
        record.resolution_trace_id = incident.resolution_trace_id
        record.closed_by = incident.closed_by
        record.closure_reason = incident.closure_reason
        record.closed_at = incident.closed_at
        record.closure_idempotency_key_hash = (
            incident.closure_idempotency_key_hash
        )
        record.closure_request_hash = incident.closure_request_hash
        record.closure_trace_id = incident.closure_trace_id
