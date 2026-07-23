import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.services.outbox_backlog import (
    OutboxBacklogMonitor,
    OutboxBacklogMonitorConfig,
)
from devops_agent_platform.domain.enums import OutboxStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.outbox_metrics import OutboxBacklogSnapshot

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


class FakeClock:
    """提供可手动推进的单调时钟。"""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SequenceReader:
    """按顺序返回快照或抛出异常。"""

    def __init__(
        self,
        outcomes: list[OutboxBacklogSnapshot | Exception],
    ) -> None:
        self._outcomes = iter(outcomes)
        self.calls = 0

    async def load_snapshot(self) -> OutboxBacklogSnapshot:
        self.calls += 1
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class BlockingReader:
    """阻塞查询，用于验证并发单飞和超时。"""

    def __init__(self, snapshot: OutboxBacklogSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def load_snapshot(self) -> OutboxBacklogSnapshot:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.snapshot


def build_snapshot(pending: int = 2) -> OutboxBacklogSnapshot:
    """构造可用于缓存断言的积压快照。"""
    return OutboxBacklogSnapshot(
        counts=(
            (OutboxStatus.PENDING, pending),
            (OutboxStatus.PROCESSING, 1),
            (OutboxStatus.FAILED, 0),
        ),
        oldest_unpublished_at=NOW - timedelta(minutes=5),
    )


async def test_successful_snapshot_is_cached_until_ttl_expires() -> None:
    clock = FakeClock()
    reader = SequenceReader([build_snapshot(2), build_snapshot(3)])
    monitor = OutboxBacklogMonitor(
        reader,
        OutboxBacklogMonitorConfig(cache_ttl=timedelta(seconds=5)),
        clock,
    )

    first = await monitor.collect()
    second = await monitor.collect()
    assert first is second
    assert reader.calls == 1

    clock.advance(5)
    refreshed = await monitor.collect()

    assert reader.calls == 2
    assert refreshed.snapshot is not None
    assert refreshed.snapshot.count_for(OutboxStatus.PENDING) == 3


async def test_concurrent_refresh_executes_only_one_query() -> None:
    clock = FakeClock()
    reader = BlockingReader(build_snapshot())
    monitor = OutboxBacklogMonitor(reader, clock=clock)

    tasks = [asyncio.create_task(monitor.collect()) for _ in range(10)]
    await reader.started.wait()
    assert reader.calls == 1
    reader.release.set()
    reports = await asyncio.gather(*tasks)

    assert reader.calls == 1
    assert all(report.source_up for report in reports)


async def test_failure_reuses_previous_snapshot_and_marks_it_stale(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    reader = SequenceReader(
        [
            build_snapshot(),
            ConnectionError("database password=backlog-secret"),
        ]
    )
    monitor = OutboxBacklogMonitor(
        reader,
        OutboxBacklogMonitorConfig(cache_ttl=timedelta(seconds=5)),
        clock,
    )
    successful = await monitor.collect()
    clock.advance(5)

    with caplog.at_level(
        logging.WARNING,
        logger=(
            "devops_agent_platform.application.services.outbox_backlog"
        ),
    ):
        failed = await monitor.collect()
    cached_failure = await monitor.collect()

    assert failed.snapshot is successful.snapshot
    assert failed.source_up is False
    assert failed.stale is True
    assert cached_failure is failed
    assert reader.calls == 2
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "读取Outbox积压指标失败"
    )
    assert record.exc_info is None
    assert "backlog-secret" not in caplog.text


async def test_first_query_timeout_returns_unavailable_without_snapshot() -> None:
    reader = BlockingReader(build_snapshot())
    monitor = OutboxBacklogMonitor(
        reader,
        OutboxBacklogMonitorConfig(
            query_timeout=timedelta(milliseconds=1),
            cache_ttl=timedelta(seconds=5),
        ),
    )

    report = await monitor.collect()

    assert report.snapshot is None
    assert report.source_up is False
    assert report.stale is False
    assert reader.calls == 1


@pytest.mark.parametrize(
    "config",
    [
        {"query_timeout": timedelta(0)},
        {"cache_ttl": timedelta(seconds=-1)},
    ],
)
def test_invalid_monitor_config_is_rejected(
    config: dict[str, timedelta],
) -> None:
    with pytest.raises(AppValidationError):
        OutboxBacklogMonitorConfig(**config)
