import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.services import (
    ticket_submission_consumer_runner as runner_module,
)
from devops_agent_platform.application.services import (
    ticket_submission_record_processor as processor_module,
)
from devops_agent_platform.domain.enums import TicketSubmissionConsumerWorkerState
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.messaging import ConsumerLagSnapshot

NOW = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
Runner = runner_module.TicketSubmissionConsumerRunner
RunnerConfig = runner_module.TicketSubmissionConsumerRunnerConfig
MessageDisposition = processor_module.TicketSubmissionMessageDisposition
ProcessingResult = processor_module.TicketSubmissionProcessingResult


def result(
    disposition: MessageDisposition,
    *,
    reason_code: str | None = None,
    reason: str | None = None,
) -> ProcessingResult:
    """构造单条工单提交消息的处理结果。"""
    return ProcessingResult(
        disposition=disposition,
        reason_code=reason_code,
        reason=reason,
    )


class SequenceConsumer:
    """按顺序返回轮询结果或异常，并记录生命周期。"""

    def __init__(
        self,
        outcomes: list[ProcessingResult | Exception | None],
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

    async def run_once(self) -> ProcessingResult | None:
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
        self.runner: Runner | None = None

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


def build_config() -> RunnerConfig:
    """构造便于断言退避范围的Runner配置。"""
    return RunnerConfig(
        active_poll_interval=timedelta(seconds=0.1),
        empty_backoff_initial=timedelta(seconds=10),
        empty_backoff_max=timedelta(seconds=30),
        retry_backoff_initial=timedelta(seconds=4),
        retry_backoff_max=timedelta(seconds=20),
        error_backoff_initial=timedelta(seconds=6),
        error_backoff_max=timedelta(seconds=24),
    )


async def test_empty_backoff_resets_after_acknowledged_message() -> None:
    """空轮询指数退避，处理消息后立即恢复活跃轮询间隔。"""
    consumer = SequenceConsumer(
        [
            None,
            result(MessageDisposition.ACK),
        ]
    )
    waiter = RecordingWaiter(stop_after=2)
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
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
    assert runner.health.state is TicketSubmissionConsumerWorkerState.STOPPED
    assert runner.health.total_polled == 1
    assert runner.health.total_acknowledged == 1


async def test_retry_degrades_then_ack_recovers_health() -> None:
    """可重试处理失败进入降级，后续ACK清除失败状态。"""
    consumer = SequenceConsumer(
        [
            result(
                MessageDisposition.RETRY,
                reason_code="PERSISTENCE_ERROR",
                reason="database unavailable",
            ),
            result(MessageDisposition.ACK),
        ]
    )
    waiter = RecordingWaiter(stop_after=2)
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
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
    """工单提交重试原因进入健康快照前必须脱敏。"""
    consumer = SequenceConsumer(
        [
            result(
                MessageDisposition.RETRY,
                reason_code="GATEWAY_ERROR",
                reason="gateway password=hunter2 token=secret-token",
            ),
        ]
    )
    waiter = RecordingWaiter(stop_after=1)
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert runner.health.last_error == (
        "gateway password=[REDACTED] token=[REDACTED]"
    )
    assert "hunter2" not in str(runner.health.last_error)
    assert "secret-token" not in str(runner.health.last_error)


async def test_dead_letter_is_counted_without_degrading_worker() -> None:
    """死信是对坏消息的确定性处置，不应长期污染Worker健康状态。"""
    consumer = SequenceConsumer(
        [
            result(
                MessageDisposition.DEAD_LETTER,
                reason_code="INVALID_JSON",
                reason="invalid json",
            )
        ]
    )
    waiter = RecordingWaiter(stop_after=1)
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert waiter.calls[0] == 0.1
    assert runner.health.total_dead_lettered == 1
    assert runner.health.last_error is None
    assert runner.health.consecutive_failures == 0


async def test_ignored_shared_topic_event_is_counted_as_ack() -> None:
    """共享Topic中的非本业务事件应ACK跳过，并单独计数便于排查。"""
    consumer = SequenceConsumer(
        [
            result(
                MessageDisposition.ACK,
                reason_code="EVENT_NOT_TARGETED",
                reason="not targeted",
            )
        ]
    )
    waiter = RecordingWaiter(stop_after=1)
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert runner.health.total_acknowledged == 1
    assert runner.health.total_ignored == 1


async def test_runner_health_copies_latest_lag_snapshot() -> None:
    """Runner健康快照应保存最近一次Lag汇总供Metrics读取。"""
    consumer = SequenceConsumer([result(MessageDisposition.ACK)])
    consumer.lag_snapshot = ConsumerLagSnapshot(
        source_up=True,
        assigned_partitions=2,
        measured_partitions=2,
        total_lag=35,
        max_partition_lag=20,
    )
    waiter = RecordingWaiter(stop_after=1)
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert runner.health.lag_source_up is True
    assert runner.health.assigned_partitions == 2
    assert runner.health.measured_partitions == 2
    assert runner.health.total_lag == 35
    assert runner.health.max_partition_lag == 20


async def test_system_error_uses_error_backoff_and_recovers() -> None:
    """Consumer系统异常不终止循环，按错误退避后继续轮询。"""
    consumer = SequenceConsumer(
        [
            RuntimeError("broker unavailable"),
            None,
        ]
    )
    waiter = RecordingWaiter(stop_after=2)
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
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
    """工单Consumer健康摘要可脱敏，日志不得保留原始异常。"""
    consumer = SequenceConsumer(
        [
            RuntimeError(
                "gateway password=hunter2 token=consumer-secret"
            )
        ]
    )
    waiter = RecordingWaiter(stop_after=1)
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
        config=build_config(),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    with caplog.at_level(
        logging.ERROR,
        logger=(
            "devops_agent_platform.application.services."
            "ticket_submission_consumer_runner"
        ),
    ):
        await runner.run()

    assert runner.health.last_error == (
        "RuntimeError: gateway password=[REDACTED] token=[REDACTED]"
    )
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "工单提交Consumer执行轮询失败"
    )
    assert record.worker_id == "ticket-worker-001"
    assert record.consecutive_failures == 1
    assert record.exc_info is None
    assert "hunter2" not in caplog.text
    assert "consumer-secret" not in caplog.text


class BlockingConsumer:
    """阻塞单条消息处理，用于验证停机、重入和取消清理。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.completed = False

    async def start(self) -> None:
        """模拟Consumer启动完成。"""

    async def run_once(self) -> ProcessingResult:
        self.started.set()
        await self.release.wait()
        self.completed = True
        return result(MessageDisposition.ACK)

    async def close(self) -> None:
        self.closed = True


async def test_stop_waits_for_current_record_and_rejects_reentry() -> None:
    """协作式停止应等待当前消息完成，再关闭Consumer。"""
    consumer = BlockingConsumer()
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
        clock=lambda: NOW,
    )
    task = asyncio.create_task(runner.run())
    await consumer.started.wait()

    with pytest.raises(RuntimeError, match="already running"):
        await runner.run()

    runner.request_stop()
    assert runner.health.state is TicketSubmissionConsumerWorkerState.STOPPING
    consumer.release.set()
    await task

    assert consumer.completed is True
    assert consumer.closed is True
    assert runner.health.total_acknowledged == 1


async def test_cancellation_propagates_and_closes_consumer() -> None:
    """强制取消仍应执行finally关闭Consumer并更新健康状态。"""
    consumer = BlockingConsumer()
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
        clock=lambda: NOW,
    )
    task = asyncio.create_task(runner.run())
    await consumer.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert consumer.closed is True
    assert runner.health.state is TicketSubmissionConsumerWorkerState.STOPPED
    assert runner.health.stopped_at == NOW


async def test_start_failure_still_closes_consumer() -> None:
    """Consumer启动失败时Runner也执行统一关闭路径。"""
    consumer = SequenceConsumer(
        [],
        start_error=RuntimeError("consumer start failed"),
    )
    runner = Runner(
        consumer=consumer,
        worker_id="ticket-worker-001",
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="consumer start failed"):
        await runner.run()

    assert consumer.close_count == 1
    assert runner.health.state is TicketSubmissionConsumerWorkerState.STOPPED


async def test_runner_instance_cannot_restart() -> None:
    """停止后的同一Runner实例不能再次启动。"""
    runner = Runner(
        consumer=SequenceConsumer([]),
        worker_id="ticket-worker-001",
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
        {
            "error_backoff_initial": timedelta(seconds=2),
            "error_backoff_max": timedelta(seconds=1),
        },
    ],
)
def test_invalid_runner_config_is_rejected(
    values: dict[str, timedelta],
) -> None:
    """危险或相互矛盾的退避配置应在启动前失败。"""
    with pytest.raises(AppValidationError):
        RunnerConfig(**values)


@pytest.mark.parametrize(
    "worker_id",
    [" ticket-worker-001", "ticket\nforged", "w" * 129],
)
def test_invalid_worker_id_is_rejected(worker_id: str) -> None:
    """Worker标识不允许污染日志、健康快照或指标上下文。"""
    with pytest.raises(AppValidationError):
        Runner(
            consumer=SequenceConsumer([]),
            worker_id=worker_id,
        )
