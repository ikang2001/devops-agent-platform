from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import (
    PersistenceError,
    ResourceBusyError,
)
from devops_agent_platform.domain.exceptions import AppValidationError


class PostgreSQLIncidentCorrelationLock:
    """使用 PostgreSQL 事务级 advisory lock 串行化事故关联决策。

    锁只覆盖相同租户和服务，其他服务仍可并行处理。事务级锁由 PostgreSQL 在
    commit/rollback 时释放，连接异常断开时也不会留下永久锁。
    """

    def __init__(
        self,
        session: AsyncSession,
        timeout_ms: int = 2000,
    ) -> None:
        self._session = session
        self._timeout_ms = self._validate_timeout(timeout_ms)

    async def acquire(self, tenant_id: str, service_name: str) -> None:
        """在当前数据库事务中获取租户和服务维度的关联锁。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("service_name", service_name, 256)
        self._ensure_postgresql()
        lock_key = self._build_lock_key(tenant_id, service_name)

        try:
            await self._session.execute(
                text(
                    "SELECT set_config("
                    "'lock_timeout', :lock_timeout, true"
                    ")"
                ),
                {"lock_timeout": f"{self._timeout_ms}ms"},
            )
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
            # 获取目标锁后恢复默认值，避免影响同一事务后续普通行锁等待。
            await self._session.execute(
                text(
                    "SELECT set_config("
                    "'lock_timeout', '0', true"
                    ")"
                )
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise ResourceBusyError(
                    "Incident correlation lock wait timed out"
                ) from exc
            raise PersistenceError(
                "Could not acquire incident correlation lock"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not acquire incident correlation lock"
            ) from exc

    def _ensure_postgresql(self) -> None:
        """拒绝在不支持事务级 advisory lock 的数据库上静默降级。"""
        bind = self._session.get_bind()
        if bind.dialect.name != "postgresql":
            raise PersistenceError(
                "Incident correlation lock requires PostgreSQL"
            )

    @staticmethod
    def _build_lock_key(tenant_id: str, service_name: str) -> int:
        """把业务复合键稳定映射为 PostgreSQL 接受的有符号 bigint。"""
        payload = (
            f"{len(tenant_id)}:{tenant_id}|"
            f"{len(service_name)}:{service_name}"
        ).encode()
        digest = sha256(payload).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    @staticmethod
    def _validate_text(field_name: str, value: str, max_length: int) -> None:
        """校验参与锁键计算的业务字段。"""
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
    def _validate_timeout(timeout_ms: int) -> int:
        """限制锁等待时间，避免错误配置长期占用请求协程。"""
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
            raise AppValidationError("timeout_ms must be an integer")
        if not 1 <= timeout_ms <= 30_000:
            raise AppValidationError(
                "timeout_ms must be between 1 and 30000"
            )
        return timeout_ms
