import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.services.rca_consumer_runner import (
    RCAConsumerRunner,
    RCAConsumerRunnerConfig,
)
from devops_agent_platform.domain.enums import RCAConsumerWorkerState
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.messaging import (
    ConsumerLagSnapshot,
    MessageConsumeResult,
)

NOW = datetime(2026, 6, 28, 19, 0, tzinfo=UTC)


def result(
    *,
    polled: int = 0,
    acknowledged: int = 0,
    retried: int = 0,
    dead_lettered: int = 0,
    retry_reason: str | None = None,
    lag_snapshot: ConsumerLagSnapshot | None = None,
) -> MessageConsumeResult:
    """构造单轮消费统计。"""
    return MessageConsumeResult(
        polled=polled,
        acknowledged=acknowledged,
        retried=retried,
        dead_lettered=dead_lettered,
        retry_reason=retry_reason,
        lag_snapshot=lag_snapshot,
    )


class SequenceConsumer:
    """按顺序返回轮询结果或异常，并记录生命周期。"""

    def __init__(
        self,
        outcomes: list[MessageConsumeResult | Exception],
        start_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self._outcomes = iter(outcomes)
        self.start_error = start_error
        self.close_error = close_error
        self.start_count = 0
        self.close_count = 0
        self.run_count = 0

    async def start(self) -> None:
        self.start_count += 1
        if self.start_error is not None:
            raise self.start_error

    async def run_once(self) -> MessageConsumeResult:
        self.run_count += 1
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class RecordingWaiter:
    """记录等待时间，并在指定轮数后请求Runner停止。"""

    def __init__(self, stop_after: int) -> None:
        self.stop_after = stop_after
        self.calls: list[float] = []
        self.runner: RCAConsumerRunner | None = None

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


def build_config() -> RCAConsumerRunnerConfig:
    """构造便于断言退避范围的Runner配置。"""
    return RCAConsumerRunnerConfig(
        active_poll_interval=timedelta(seconds=0.1),
        empty_backoff_initial=timedelta(seconds=10),
        empty_backoff_max=timedelta(seconds=30),
        retry_backoff_initial=timedelta(seconds=4),
        retry_backoff_max=timedelta(seconds=20),
        error_backoff_initial=timedelta(seconds=6),
        error_backoff_max=timedelta(seconds=24),
    )


async def test_empty_backoff_resets_after_acknowledged_message() -> None:
    """空轮询指数退避，处理消息后立即恢复活跃间隔。"""
    consumer = SequenceConsumer(
        [
            result(),
            result(polled=1, acknowledged=1),
        ]
    )
    waiter = RecordingWaiter(stop_after=2)
    runner = RCAConsumerRunner(
        consumer=consumer,
        worker_id="rca-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert 5 <= waiter.calls[0] <= 10
    assert waiter.calls[1] == 0.1
    assert consumer.start_count == 1
    assert consumer.close_count == 1
    assert runner.health.state is RCAConsumerWorkerState.STOPPED
    assert runner.health.total_polled == 1
    assert runner.health.total_acknowledged == 1


async def test_retry_degrades_then_success_recovers_health() -> None:
    """显式重试进入降级和退避，后续ACK清除失败状态。"""
    consumer = SequenceConsumer(
        [
            result(
                polled=1,
                retried=1,
                retry_reason="database unavailable",
            ),
            result(polled=1, acknowledged=1),
        ]
    )
    waiter = RecordingWaiter(stop_after=2)
    runner = RCAConsumerRunner(
        consumer=consumer,
        worker_id="rca-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert 2 <= waiter.calls[0] <= 4
    assert waiter.calls[1] == 0.1
    assert runner.health.total_retried == 1
    assert runner.health.consecutive_failures == 0
    assert runner.health.last_error is None
    assert runner.health.last_success_at == NOW


async def test_retry_reason_is_redacted_in_health_snapshot() -> None:
    """重试原因进入健康快照前必须脱敏，避免监控面泄漏凭证。"""
    consumer = SequenceConsumer(
        [
            result(
                polled=1,
                retried=1,
                retry_reason="database password=hunter2 token=secret-token",
            ),
        ]
    )
    waiter = RecordingWaiter(stop_after=1)
    runner = RCAConsumerRunner(
        consumer=consumer,
        worker_id="rca-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert runner.health.last_error == (
        "database password=[REDACTED] token=[REDACTED]"
    )
    assert "hunter2" not in str(runner.health.last_error)
    assert "secret-token" not in str(runner.health.last_error)


async def test_runner_health_copies_latest_lag_snapshot() -> None:
    """Runner健康快照应保存最近一次Lag汇总供Metrics读取。"""
    consumer = SequenceConsumer(
        [
            result(
                lag_snapshot=ConsumerLagSnapshot(
                    source_up=True,
                    assigned_partitions=3,
                    measured_partitions=3,
                    total_lag=42,
                    max_partition_lag=20,
                )
            )
        ]
    )
    waiter = RecordingWaiter(stop_after=1)
    runner = RCAConsumerRunner(
        consumer=consumer,
        worker_id="rca-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert runner.health.lag_source_up is True
    assert runner.health.assigned_partitions == 3
    assert runner.health.measured_partitions == 3
    assert runner.health.total_lag == 42
    assert runner.health.max_partition_lag == 20


async def test_system_error_uses_error_backoff_and_recovers() -> None:
    """Consumer异常不终止循环，按系统错误退避后继续轮询。"""
    consumer = SequenceConsumer(
        [
            RuntimeError("broker unavailable"),
            result(dead_lettered=0),
        ]
    )
    waiter = RecordingWaiter(stop_after=2)
    runner = RCAConsumerRunner(
        consumer=consumer,
        worker_id="rca-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert 3 <= waiter.calls[0] <= 6
    assert 5 <= waiter.calls[1] <= 10
    assert runner.health.consecutive_failures == 0
    assert runner.health.last_error is None


async def test_system_error_log_excludes_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """系统错误保留脱敏健康摘要，但日志不能附带原始异常。"""
    consumer = SequenceConsumer(
        [
            RuntimeError(
                "broker password=hunter2 token=consumer-secret"
            )
        ]
    )
    waiter = RecordingWaiter(stop_after=1)
    runner = RCAConsumerRunner(
        consumer=consumer,
        worker_id="rca-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    with caplog.at_level(
        logging.ERROR,
        logger=(
            "devops_agent_platform.application.services."
            "rca_consumer_runner"
        ),
    ):
        await runner.run()

    assert runner.health.last_error == (
        "RuntimeError: broker password=[REDACTED] token=[REDACTED]"
    )
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "RCA Consumer执行轮询失败"
    )
    assert record.worker_id == "rca-worker-001"
    assert record.consecutive_failures == 1
    assert record.exc_info is None
    assert "hunter2" not in caplog.text
    assert "consumer-secret" not in caplog.text


class BlockingConsumer:
    """阻塞单条处理，用于验证停机、重入和取消清理。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.completed = False

    async def start(self) -> None:
        """模拟Consumer启动完成。"""

    async def run_once(self) -> MessageConsumeResult:
        self.started.set()
        await self.release.wait()
        self.completed = True
        return result(polled=1, acknowledged=1)

    async def close(self) -> None:
        self.closed = True


async def test_stop_waits_for_current_record_and_rejects_reentry() -> None:
    """协作式停止应等待当前消息完成，再关闭Consumer。"""
    consumer = BlockingConsumer()
    runner = RCAConsumerRunner(
        consumer=consumer,
        worker_id="rca-worker-001",
        clock=lambda: NOW,
    )
    task = asyncio.create_task(runner.run())
    await consumer.started.wait()

    with pytest.raises(RuntimeError, match="already running"):
        await runner.run()

    runner.request_stop()
    assert runner.health.state is RCAConsumerWorkerState.STOPPING
    consumer.release.set()
    await task

    assert consumer.completed is True
    assert consumer.closed is True
    assert runner.health.total_acknowledged == 1


async def test_cancellation_propagates_and_closes_consumer() -> None:
    """强制取消仍应执行finally关闭Consumer并更新健康状态。"""
    consumer = BlockingConsumer()
    runner = RCAConsumerRunner(
        consumer=consumer,
        worker_id="rca-worker-001",
        clock=lambda: NOW,
    )
    task = asyncio.create_task(runner.run())
    await consumer.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert consumer.closed is True
    assert runner.health.state is RCAConsumerWorkerState.STOPPED
    assert runner.health.stopped_at == NOW


async def test_start_failure_still_closes_consumer() -> None:
    """Consumer启动失败时Runner也执行统一关闭路径。"""
    consumer = SequenceConsumer(
        [],
        start_error=RuntimeError("consumer start failed"),
    )
    runner = RCAConsumerRunner(
        consumer=consumer,
        worker_id="rca-worker-001",
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="consumer start failed"):
        await runner.run()

    assert consumer.close_count == 1
    assert runner.health.state is RCAConsumerWorkerState.STOPPED


async def test_runner_instance_cannot_restart() -> None:
    """停止后的同一Runner实例不能再次启动。"""
    runner = RCAConsumerRunner(
        consumer=SequenceConsumer([]),
        worker_id="rca-worker-001",
        clock=lambda: NOW,
    )
    runner.request_stop()
    await runner.run()

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        await runner.run()


@pytest.mark.parametrize(
    "values",
    [
        {"active_poll_interval": timedelta(0)},
        {
            "empty_backoff_initial": timedelta(seconds=2),
            "empty_backoff_max": timedelta(seconds=1),
        },
        {
            "retry_backoff_initial": timedelta(seconds=2),
            "retry_backoff_max": timedelta(seconds=1),
        },
    ],
)
def test_invalid_runner_config_is_rejected(
    values: dict[str, timedelta],
) -> None:
    """危险或相互矛盾的退避配置应在启动前失败。"""
    with pytest.raises(AppValidationError):
        RCAConsumerRunnerConfig(**values)


@pytest.mark.parametrize(
    "worker_id",
    [" rca-worker-001", "rca\nforged", "w" * 129],
)
def test_invalid_worker_id_is_rejected(worker_id: str) -> None:
    """Consumer Worker身份不能污染日志、健康快照或租约字段。"""
    with pytest.raises(AppValidationError, match="worker_id"):
        RCAConsumerRunner(
            consumer=SequenceConsumer([]),
            worker_id=worker_id,
            clock=lambda: NOW,
        )
