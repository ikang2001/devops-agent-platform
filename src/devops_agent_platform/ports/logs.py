from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class LogStream:
    """Loki响应中的不可变日志流。"""

    labels: tuple[tuple[str, str], ...]
    entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LogsRangeResult:
    """一次日志范围查询的有限流集合。"""

    streams: tuple[LogStream, ...]
    possibly_truncated: bool


class LogsRangeQueryPort(Protocol):
    """执行一个受控Loki日志范围查询。"""

    async def query_range(
        self,
        *,
        tenant_id: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int,
        trace_id: str,
    ) -> LogsRangeResult:
        """返回经过容量校验的日志流。"""
        ...
