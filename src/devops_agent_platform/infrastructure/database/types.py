from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from devops_agent_platform.domain.exceptions import AppValidationError


class UTCDateTime(TypeDecorator[datetime]):
    """统一数据库驱动之间的 UTC 时间读写语义。

    PostgreSQL 通常返回带时区时间，而 SQLite 等测试驱动可能返回无时区对象。
    该类型在边界处统一转换，避免时间比较结果依赖运行环境。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        """写入前拒绝无时区时间，并转换为 UTC。"""
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise AppValidationError("datetime must include timezone information")
        return value.astimezone(UTC)

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        """读取后保证调用方始终得到带 UTC 时区的对象。"""
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @property
    def python_type(self) -> type[Any]:
        """向 SQLAlchemy 暴露该列对应的 Python 类型。"""
        return datetime
