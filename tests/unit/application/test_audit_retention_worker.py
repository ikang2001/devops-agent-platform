import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.services.audit_retention_service import (
    AuditRetentionResult,
)
from devops_agent_platform.application.services.audit_retention_worker import (
    AuditRetentionWorkerConfig,
    AuditRetentionWorkerRunner,
)
from devops_agent_platform.domain.enums import AuditRetentionWorkerState
from devops_agent_platform.domain.exceptions import AppValidationError

NOW = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)


def build_result(count: int) -> AuditRetentionResult:
    """构造单批清理结果。"""
    return AuditRetentionResult(
        cutoff=NOW - timedelta(days=30),
        purged_at=NOW,
        purged_workflow_runs=count,
    )


class SequenceRetentionService:
    """按给定顺序返回结果或抛出异常。"""

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = iter(outcomes)
        self.calls = 0

    async def purge_once(self) -> AuditRetentionResult:
        self.calls += 1
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


class StopAfterTwoWaits:
    """记录两次等待时长，并在第二轮后请求停止。"""

    def __init__(self) -> None:
        self.runner: AuditRetentionWorkerRunner | None = None
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


async def test_worker_uses_active_then_idle_interval_and_updates_health() -> None:
    """满批次快速继续，未满批次进入长空闲间隔。"""
    service = SequenceRetentionService(
        [build_result(10), build_result(3)]
    )
    waiter = StopAfterTwoWaits()
    runner = AuditRetentionWorkerRunner(
        service,
        worker_id="retention-worker-001",
        config=AuditRetentionWorkerConfig(
            active_interval=timedelta(seconds=1),
            idle_interval=timedelta(seconds=60),
            batch_size=10,
        ),
        clock=lambda: NOW,
        wait_for_stop=waiter,
    )
    waiter.runner = runner

    await runner.run()

    assert waiter.delays == [1.0, 60.0]
    assert service.calls == 2
    assert runner.health.state is AuditRetentionWorkerState.STOPPED
    assert runner.health.total_cycles == 2
    assert runner.health.total_purged_workflow_runs == 13
    assert runner.health.last_success_at == NOW


async def test_worker_errors_use_capped_backoff_and_recover_health(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """连续失败应指数退避，成功后清零失败状态。"""
    service = SequenceRetentionService(
        [
            RuntimeError("database password=do-not-log"),
            RuntimeError("database unavailable"),
            build_result(0),
        ]
    )
    runner = AuditRetentionWorkerRunner(
        service,
        worker_id="retention-worker-001",
        config=AuditRetentionWorkerConfig(
            error_backoff_initial=timedelta(seconds=2),
            error_backoff_max=timedelta(seconds=3),
        ),
        clock=lambda: NOW,
    )

    first_delay = await runner._run_cycle()
    second_delay = await runner._run_cycle()
    assert timedelta(seconds=1) <= first_delay <= timedelta(seconds=2)
    assert timedelta(seconds=1.5) <= second_delay <= timedelta(seconds=3)
    assert runner.health.state is AuditRetentionWorkerState.DEGRADED
    assert runner.health.consecutive_failures == 2
    assert "do-not-log" not in str(runner.health.last_error)
    logged = " ".join(
        f"{record.getMessage()} {getattr(record, 'error', '')}"
        for record in caplog.records
    )
    assert "do-not-log" not in logged

    await runner._run_cycle()
    assert runner.health.state is AuditRetentionWorkerState.RUNNING
    assert runner.health.consecutive_failures == 0
    assert runner.health.last_error is None


async def test_request_stop_interrupts_idle_wait() -> None:
    """协作式停止应立即唤醒长时间空闲等待。"""
    service = SequenceRetentionService([build_result(0)])
    runner = AuditRetentionWorkerRunner(
        service,
        worker_id="retention-worker-001",
        config=AuditRetentionWorkerConfig(
            idle_interval=timedelta(hours=1)
        ),
        clock=lambda: NOW,
    )
    task = asyncio.create_task(runner.run())
    while service.calls == 0:
        await asyncio.sleep(0)

    runner.request_stop()
    await asyncio.wait_for(task, timeout=1)

    assert runner.health.state is AuditRetentionWorkerState.STOPPED
    assert runner.health.stopped_at == NOW


async def test_worker_instance_cannot_restart() -> None:
    """停止后的 Runner 不能复用，避免累计状态与 Event 污染新任务。"""
    service = SequenceRetentionService([build_result(0)])
    runner = AuditRetentionWorkerRunner(
        service,
        worker_id="retention-worker-001",
        clock=lambda: NOW,
    )
    runner.request_stop()
    await runner.run()

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        await runner.run()


@pytest.mark.parametrize(
    "config",
    [
        {"active_interval": timedelta(0)},
        {"idle_interval": timedelta(0)},
        {
            "error_backoff_initial": timedelta(seconds=2),
            "error_backoff_max": timedelta(seconds=1),
        },
        {"batch_size": True},
        {"batch_size": 1001},
    ],
)
def test_worker_config_rejects_unsafe_values(config: dict) -> None:
    """Runner 配置必须保留等待和批次保护。"""
    with pytest.raises(AppValidationError):
        AuditRetentionWorkerConfig(**config)


@pytest.mark.parametrize(
    "worker_id",
    [" retention-worker-001", "retention\nforged", "w" * 129],
)
def test_invalid_worker_id_is_rejected(worker_id: str) -> None:
    """清理 Worker 身份不能污染日志或退避抖动种子。"""
    with pytest.raises(AppValidationError, match="worker_id"):
        AuditRetentionWorkerRunner(
            SequenceRetentionService([]),
            worker_id=worker_id,
            clock=lambda: NOW,
        )
