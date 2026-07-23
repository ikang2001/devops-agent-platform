# 这段代码是 告警数据的数据库仓储适配器：它负责把领域层的 Alert 对象转换成 SQLAlchemy
# 记录并写入数据库，但只执行 flush，不负责 commit/rollback，同时把 SQLAlchemy 底层异
# 常转换成项目统一的 ConflictError 或 PersistenceError，
# 方便上层统一处理事务和错误响应。
# 把告警存进数据库；
# 重复了就告诉上层“冲突”；
# 数据库坏了就告诉上层“持久化失败”；
# 至于最后要不要提交事务，交给外层统一决定。

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.infrastructure.database.mappers.alert import AlertMapper
from devops_agent_platform.infrastructure.database.models.alert import AlertRecord


class SQLAlchemyAlertRepository:
    """基于 SQLAlchemy AsyncSession 的告警仓储适配器。

    该适配器只执行 ``flush``，不负责提交或回滚事务。事务边界由调用它的应用用例
    统一控制，确保未来写入告警、事故和审计记录时可以整体成功或整体失败。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, alert: Alert) -> None:
        """写入一条告警记录并把数据库异常转换为稳定的应用异常。

        参数：
            alert: 已通过领域层构造的告警事实，开始时间必须包含时区。

        异常：
            AppValidationError: 持久化映射所需的数据不符合约束。
            ConflictError: 主键或未来新增的唯一约束冲突。
            PersistenceError: 数据库连接、SQL 执行等非业务故障。
        """
        record = AlertMapper.to_record(alert)
        try:
            self._session.add(record)
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"Alert persistence conflict: {alert.alert_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist alert") from exc

    async def get_by_external_event_id(
        self,
        tenant_id: str,
        source: str,
        external_event_id: str,
    ) -> Alert | None:
        """按完整幂等键精确查询告警。

        查询条件始终包含租户，避免相同上游事件 ID 在租户之间相互干扰。SQLAlchemy
        使用绑定参数，不会把调用方输入直接拼接到 SQL。
        """
        self._validate_lookup_value("tenant_id", tenant_id, 128)
        self._validate_lookup_value("source", source, 128)
        self._validate_lookup_value("external_event_id", external_event_id, 256)

        statement = (
            select(AlertRecord)
            .where(
                AlertRecord.tenant_id == tenant_id,
                AlertRecord.source == source,
                AlertRecord.external_event_id == external_event_id,
            )
            .limit(1)
        )
        try:
            record = await self._session.scalar(statement)
            return AlertMapper.to_domain(record) if record is not None else None
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load alert") from exc
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError(
                "Stored alert violates the domain contract"
            ) from exc

    @staticmethod
    def _validate_lookup_value(
        field_name: str,
        value: str,
        max_length: int,
    ) -> None:
        """校验查询键，避免空查询和带歧义的首尾空白。"""
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
