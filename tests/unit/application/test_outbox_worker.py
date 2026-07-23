import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.services.outbox_dispatcher import (
    OutboxDispatchResult,
)
from devops_agent_platform.application.services.outbox_worker import (
    OutboxWorkerConfig,
    OutboxWorkerRunner,
)
from devops_agent_platform.domain.enums import OutboxWorkerState
from devops_agent_platform.domain.exceptions import AppValidationError

NOW = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)


def result(
    claimed: int = 0,
    published: int = 0,
    retried: int = 0,
    failed: int = 0,
) -> OutboxDispatchResult:
    """构造单轮发布统计。"""
    return OutboxDispatchResult(
        claimed=claimed,
        published=published,
        retried=retried,
        failed=failed,
    )


class SequenceDispatcher:
    """按顺序返回结果或抛出异常。"""

    def __init__(
        self,
        outcomes: list[OutboxDispatchResult | Exception],
    ) -> None:
        self._outcomes = iter(outcomes)
        self.calls = 0

    async def run_once(self) -> OutboxDispatchResult:
        self.calls += 1
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingWaiter:
    """记录退避时间，并在指定次数后请求停止。"""

    def __init__(self, stop_after: int) -> None:
        self.stop_after = stop_after
        self.calls: list[float] = []
        self.runner: OutboxWorkerRunner | None = None

    async def __call__(
        self,
        stop_event: asyncio.Event,
        timeout_seconds: float,
    ) -> bool:
        self.calls.append(timeout_seconds)
        if len(self.calls) >= self.stop_after:
            assert self.runner is not None
            self.runner.request_stop()
            return True
        return False


def build_config() -> OutboxWorkerConfig:
    """返回便于断言退避范围的Worker配置。"""
    return OutboxWorkerConfig(
        poll_interval=timedelta(seconds=0.2),
        empty_backoff_initial=timedelta(seconds=10),
        empty_backoff_max=timedelta(seconds=30),
        error_backoff_initial=timedelta(seconds=4),
        error_backoff_max=timedelta(seconds=20),
    )


async def test_empty_backoff_resets_after_processing_messages() -> None:
    dispatcher = SequenceDispatcher(
        [
            result(),
            result(claimed=2, published=2),
        ]
    )
    waiter = RecordingWaiter(stop_after=2)
    runner = OutboxWorkerRunner(
        dispatcher=dispatcher,
        worker_id="worker_001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert 5 <= waiter.calls[0] <= 10
    assert waiter.calls[1] == 0.2
    assert runner.health.state is OutboxWorkerState.STOPPED
    assert runner.health.total_claimed == 2
    assert runner.health.total_published == 2
    assert runner.health.consecutive_failures == 0


async def test_worker_recovers_from_system_error() -> None:
    dispatcher = SequenceDispatcher(
        [
            RuntimeError("database unavailable"),
            result(),
        ]
    )
    waiter = RecordingWaiter(stop_after=2)
    runner = OutboxWorkerRunner(
        dispatcher=dispatcher,
        worker_id="worker_001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert 2 <= waiter.calls[0] <= 4
    assert 5 <= waiter.calls[1] <= 10
    assert runner.health.consecutive_failures == 0
    assert runner.health.last_error is None
    assert runner.health.last_success_at == NOW


async def test_worker_error_is_redacted_in_health_snapshot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = SequenceDispatcher(
        [
            RuntimeError("database password=hunter2 token=secret-token"),
        ]
    )
    waiter = RecordingWaiter(stop_after=1)
    runner = OutboxWorkerRunner(
        dispatcher=dispatcher,
        worker_id="worker_001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    with caplog.at_level(
        logging.ERROR,
        logger=(
            "devops_agent_platform.application.services.outbox_worker"
        ),
    ):
        await runner.run()

    assert runner.health.last_error == (
        "RuntimeError: database password=[REDACTED] token=[REDACTED]"
    )
    assert "hunter2" not in str(runner.health.last_error)
    assert "secret-token" not in str(runner.health.last_error)
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "Outbox Worker执行批次失败"
    )
    assert record.worker_id == "worker_001"
    assert record.consecutive_failures == 1
    assert record.exc_info is None
    assert "hunter2" not in caplog.text
    assert "secret-token" not in caplog.text


class BlockingDispatcher:
    """阻塞当前批次，用于验证防重入、停机和取消。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = False

    async def run_once(self) -> OutboxDispatchResult:
        self.started.set()
        await self.release.wait()
        self.completed = True
        return result(claimed=1, published=1)


async def test_stop_waits_for_current_batch_and_rejects_reentry() -> None:
    dispatcher = BlockingDispatcher()
    runner = OutboxWorkerRunner(
        dispatcher=dispatcher,
        worker_id="worker_001",
        clock=lambda: NOW,
    )
    task = asyncio.create_task(runner.run())
    await dispatcher.started.wait()

    with pytest.raises(RuntimeError, match="already running"):
        await runner.run()

    runner.request_stop()
    assert runner.health.state is OutboxWorkerState.STOPPING
    dispatcher.release.set()
    await task

    assert dispatcher.completed is True
    assert runner.health.state is OutboxWorkerState.STOPPED
    assert runner.health.total_published == 1


async def test_cancellation_propagates_and_cleans_health_state() -> None:
    dispatcher = BlockingDispatcher()
    runner = OutboxWorkerRunner(
        dispatcher=dispatcher,
        worker_id="worker_001",
        clock=lambda: NOW,
    )
    task = asyncio.create_task(runner.run())
    await dispatcher.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runner.health.state is OutboxWorkerState.STOPPED
    assert runner.health.stopped_at == NOW


async def test_runner_instance_cannot_restart() -> None:
    runner = OutboxWorkerRunner(
        dispatcher=SequenceDispatcher([]),
        worker_id="worker_001",
        clock=lambda: NOW,
    )
    runner.request_stop()
    await runner.run()

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        await runner.run()


@pytest.mark.parametrize(
    "values",
    [
        {"poll_interval": timedelta(0)},
        {"empty_backoff_initial": timedelta(seconds=2),
         "empty_backoff_max": timedelta(seconds=1)},
        {"error_backoff_initial": timedelta(seconds=2),
         "error_backoff_max": timedelta(seconds=1)},
    ],
)
def test_invalid_worker_config_is_rejected(
    values: dict[str, timedelta],
) -> None:
    with pytest.raises(AppValidationError):
        OutboxWorkerConfig(**values)


def test_backoff_calculation_is_bounded_for_very_large_failure_streak() -> None:
    runner = OutboxWorkerRunner(
        dispatcher=SequenceDispatcher([]),
        worker_id="worker_001",
        clock=lambda: NOW,
    )

    delay = runner._backoff_delay(
        initial=timedelta(seconds=1),
        maximum=timedelta(seconds=30),
        streak=1_000_000,
        category="error",
    )

    assert timedelta(seconds=15) <= delay <= timedelta(seconds=30)


@pytest.mark.parametrize(
    "worker_id",
    [" worker_001", "worker\nforged", "w" * 129],
)
def test_invalid_worker_id_is_rejected(worker_id: str) -> None:
    """Worker身份不能污染日志、健康快照或退避抖动种子。"""
    with pytest.raises(AppValidationError, match="worker_id"):
        OutboxWorkerRunner(
            dispatcher=SequenceDispatcher([]),
            worker_id=worker_id,
            clock=lambda: NOW,
        )
