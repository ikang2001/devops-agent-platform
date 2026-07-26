import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.services.remediation_reclaim_worker import (
    RemediationReclaimWorkerConfig,
    RemediationReclaimWorkerRunner,
)
from devops_agent_platform.domain.enums import RemediationReclaimWorkerState
from devops_agent_platform.domain.exceptions import AppValidationError

NOW = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)


class SequenceReclaimService:
    """按给定顺序返回有界回收结果或抛出异常。"""

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = iter(outcomes)
        self.limits: list[int] = []

    async def reclaim_stale(self, *, limit: int = 50) -> list[object]:
        self.limits.append(limit)
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


class StopAfterTwoWaits:
    """记录两次轮询等待，并在第二轮后停止 Runner。"""

    def __init__(self) -> None:
        self.runner: RemediationReclaimWorkerRunner | None = None
        self.delays: list[float] = []

    async def __call__(
        self,
        stop_event: asyncio.Event,
        timeout_seconds: float,
    ) -> bool:
        del stop_event
        self.delays.append(timeout_seconds)
        if len(self.delays) >= 2:
            assert self.runner is not None
            self.runner.request_stop()
            return True
        return False


class BlockingReclaimService:
    """阻塞在单批回收中，用于验证外层取消语义。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def reclaim_stale(self, *, limit: int = 50) -> list[object]:
        del limit
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


async def test_worker_polls_bounded_batches_and_updates_health() -> None:
    service = SequenceReclaimService([[object(), object()], [object()]])
    waiter = StopAfterTwoWaits()
    runner = RemediationReclaimWorkerRunner(
        service,
        worker_id="remediation-reclaim-001",
        config=RemediationReclaimWorkerConfig(
            interval=timedelta(seconds=30),
            batch_size=7,
        ),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert service.limits == [7, 7]
    assert waiter.delays == [30.0, 30.0]
    assert runner.health.state is RemediationReclaimWorkerState.STOPPED
    assert runner.health.total_cycles == 2
    assert runner.health.total_reclaimed_plans == 3
    assert runner.health.last_success_at == NOW


async def test_worker_errors_back_off_without_leaking_secrets_and_recover(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = SequenceReclaimService(
        [
            RuntimeError("database password=do-not-log"),
            RuntimeError("database unavailable"),
            [],
        ]
    )
    runner = RemediationReclaimWorkerRunner(
        service,
        worker_id="remediation-reclaim-001",
        config=RemediationReclaimWorkerConfig(
            error_backoff_initial=timedelta(seconds=2),
            error_backoff_max=timedelta(seconds=3),
        ),
        clock=lambda: NOW,
    )

    first_delay = await runner._run_cycle()
    second_delay = await runner._run_cycle()

    assert timedelta(seconds=1) <= first_delay <= timedelta(seconds=2)
    assert timedelta(seconds=1.5) <= second_delay <= timedelta(seconds=3)
    assert runner.health.state is RemediationReclaimWorkerState.DEGRADED
    assert runner.health.consecutive_failures == 2
    assert "do-not-log" not in str(runner.health.last_error)
    assert "do-not-log" not in caplog.text

    await runner._run_cycle()

    assert runner.health.state is RemediationReclaimWorkerState.RUNNING
    assert runner.health.consecutive_failures == 0
    assert runner.health.last_error is None


async def test_request_stop_interrupts_long_poll_interval() -> None:
    service = SequenceReclaimService([[]])
    runner = RemediationReclaimWorkerRunner(
        service,
        worker_id="remediation-reclaim-001",
        config=RemediationReclaimWorkerConfig(interval=timedelta(hours=1)),
        clock=lambda: NOW,
    )
    task = asyncio.create_task(runner.run())
    while not service.limits:
        await asyncio.sleep(0)

    runner.request_stop()
    await asyncio.wait_for(task, timeout=1)

    assert runner.health.state is RemediationReclaimWorkerState.STOPPED
    assert runner.health.stopped_at == NOW


async def test_outer_cancellation_propagates_through_active_cycle() -> None:
    service = BlockingReclaimService()
    runner = RemediationReclaimWorkerRunner(
        service,
        worker_id="remediation-reclaim-001",
        clock=lambda: NOW,
    )
    task = asyncio.create_task(runner.run())
    await service.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert service.cancelled is True
    assert runner.health.state is RemediationReclaimWorkerState.STOPPED
    assert runner.health.stopped_at == NOW


async def test_worker_instance_cannot_restart() -> None:
    runner = RemediationReclaimWorkerRunner(
        SequenceReclaimService([[]]),
        worker_id="remediation-reclaim-001",
        clock=lambda: NOW,
    )
    runner.request_stop()
    await runner.run()

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        await runner.run()


@pytest.mark.parametrize(
    "config",
    [
        {"interval": timedelta(0)},
        {"error_backoff_initial": timedelta(0)},
        {
            "error_backoff_initial": timedelta(seconds=2),
            "error_backoff_max": timedelta(seconds=1),
        },
        {"batch_size": True},
        {"batch_size": 1001},
    ],
)
def test_worker_config_rejects_unsafe_values(config: dict) -> None:
    with pytest.raises(AppValidationError):
        RemediationReclaimWorkerConfig(**config)


@pytest.mark.parametrize(
    "worker_id",
    [" remediation-reclaim-001", "remediation\nforged", "w" * 129],
)
def test_invalid_worker_id_is_rejected(worker_id: str) -> None:
    with pytest.raises(AppValidationError, match="worker_id"):
        RemediationReclaimWorkerRunner(
            SequenceReclaimService([]),
            worker_id=worker_id,
            clock=lambda: NOW,
        )
