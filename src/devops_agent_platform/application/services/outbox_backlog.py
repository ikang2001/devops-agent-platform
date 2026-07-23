import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.outbox_metrics import (
    OutboxBacklogSnapshot,
    OutboxMetricsReaderPort,
)

logger = logging.getLogger(__name__)
MonotonicClock = Callable[[], float]


@dataclass(frozen=True)
class OutboxBacklogMonitorConfig:
    """Outbox监控查询的超时和缓存边界。"""

    query_timeout: timedelta = timedelta(seconds=1)
    cache_ttl: timedelta = timedelta(seconds=5)

    def __post_init__(self) -> None:
        if self.query_timeout <= timedelta(0):
            raise AppValidationError("query_timeout must be positive")
        if self.cache_ttl < timedelta(0):
            raise AppValidationError("cache_ttl must not be negative")


@dataclass(frozen=True)
class OutboxBacklogReport:
    """供Prometheus适配器消费的采集结果。"""

    snapshot: OutboxBacklogSnapshot | None
    source_up: bool
    stale: bool


class OutboxBacklogMonitor:
    """使用短缓存和单飞锁保护Outbox监控聚合查询。"""

    def __init__(
        self,
        reader: OutboxMetricsReaderPort,
        config: OutboxBacklogMonitorConfig | None = None,
        clock: MonotonicClock = monotonic,
    ) -> None:
        self._reader = reader
        self._config = config or OutboxBacklogMonitorConfig()
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cached_report: OutboxBacklogReport | None = None
        self._last_attempt_at = float("-inf")

    async def collect(self) -> OutboxBacklogReport:
        """读取新快照；查询失败时显式返回不可用或陈旧状态。"""
        now = self._clock()
        if self._cache_is_fresh(now):
            return self._cached_report_or_empty()

        async with self._lock:
            now = self._clock()
            if self._cache_is_fresh(now):
                return self._cached_report_or_empty()
            report = await self._refresh()
            self._cached_report = report
            self._last_attempt_at = self._clock()
            return report

    async def _refresh(self) -> OutboxBacklogReport:
        """执行有超时的查询，并保留上次成功快照作为降级数据。"""
        try:
            snapshot = await asyncio.wait_for(
                self._reader.load_snapshot(),
                timeout=self._config.query_timeout.total_seconds(),
            )
            if not isinstance(snapshot, OutboxBacklogSnapshot):
                raise TypeError("reader returned an invalid snapshot")
            return OutboxBacklogReport(
                snapshot=snapshot,
                source_up=True,
                stale=False,
            )
        except Exception:
            previous_snapshot = (
                self._cached_report.snapshot
                if self._cached_report is not None
                else None
            )
            logger.warning("读取Outbox积压指标失败")
            return OutboxBacklogReport(
                snapshot=previous_snapshot,
                source_up=False,
                stale=previous_snapshot is not None,
            )

    def _cache_is_fresh(self, now: float) -> bool:
        """成功和失败结果都在短窗口内复用，避免故障时重试风暴。"""
        return (
            self._cached_report is not None
            and now - self._last_attempt_at
            < self._config.cache_ttl.total_seconds()
        )

    def _cached_report_or_empty(self) -> OutboxBacklogReport:
        """返回已存在缓存，并让类型检查器确认其非空。"""
        if self._cached_report is None:
            return OutboxBacklogReport(
                snapshot=None,
                source_up=False,
                stale=False,
            )
        return self._cached_report
