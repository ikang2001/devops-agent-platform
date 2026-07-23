from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from devops_agent_platform.domain.enums import OutboxStatus
from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class OutboxBacklogSnapshot:
    """数据库中Outbox积压状态的不可变只读快照。"""

    counts: tuple[tuple[OutboxStatus, int], ...]
    oldest_unpublished_at: datetime | None

    def __post_init__(self) -> None:
        """拒绝重复状态、负数计数和无时区时间。"""
        statuses = [status for status, _ in self.counts]
        if len(statuses) != len(set(statuses)):
            raise AppValidationError("outbox snapshot contains duplicate statuses")
        if any(
            not isinstance(status, OutboxStatus)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for status, count in self.counts
        ):
            raise AppValidationError("outbox snapshot counts are invalid")
        timestamp = self.oldest_unpublished_at
        if timestamp is not None and (
            not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise AppValidationError(
                "oldest_unpublished_at must include timezone information"
            )

    def count_for(self, status: OutboxStatus) -> int:
        """返回指定状态计数，缺失状态按零处理。"""
        return dict(self.counts).get(status, 0)


class OutboxMetricsReaderPort(Protocol):
    """应用监控服务读取Outbox聚合快照所依赖的端口。"""

    async def load_snapshot(self) -> OutboxBacklogSnapshot:
        """执行一次只读聚合查询。"""
        ...
