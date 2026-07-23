import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol

from devops_agent_platform.application.services.audit_retention_service import (
    AuditRetentionResult,
)
from devops_agent_platform.domain.enums import AuditRetentionWorkerState
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.tools.sanitization import redact_sensitive_text

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
WaitForStop = Callable[[asyncio.Event, float], Awaitable[bool]]


class AuditRetentionServicePort(Protocol):
    """清理 Runner 依赖的单批次应用能力。"""

    async def purge_once(self) -> AuditRetentionResult:
        """执行一个有界清理批次。"""
        ...


@dataclass(frozen=True)
class AuditRetentionWorkerConfig:
    """清理 Worker 的活跃、空闲和异常等待策略。"""

    active_interval: timedelta = timedelta(seconds=1)
    idle_interval: timedelta = timedelta(hours=1)
    error_backoff_initial: timedelta = timedelta(seconds=5)
    error_backoff_max: timedelta = timedelta(minutes=5)
    batch_size: int = 100

    def __post_init__(self) -> None:
        """校验等待时间和用于判断满批次的容量。"""
        durations = {
            "active_interval": self.active_interval,
            "idle_interval": self.idle_interval,
            "error_backoff_initial": self.error_backoff_initial,
            "error_backoff_max": self.error_backoff_max,
        }
        for field_name, value in durations.items():
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise AppValidationError(f"{field_name} must be positive")
        if self.error_backoff_initial > self.error_backoff_max:
            raise AppValidationError(
                "error_backoff_initial must not exceed error_backoff_max"
            )
        if (
            isinstance(self.batch_size, bool)
            or not isinstance(self.batch_size, int)
            or not 1 <= self.batch_size <= 1000
        ):
            raise AppValidationError("batch_size must be between 1 and 1000")


@dataclass(frozen=True)
class AuditRetentionWorkerHealth:
    """供就绪检查和监控读取的不可变清理健康快照。"""

    state: AuditRetentionWorkerState
    started_at: datetime | None
    stopped_at: datetime | None
    last_cycle_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    total_cycles: int
    total_purged_workflow_runs: int


class AuditRetentionWorkerRunner:
    """持续执行有限清理批次，并管理退避、健康与协作式停机。"""

    def __init__(
        self,
        service: AuditRetentionServicePort,
        worker_id: str,
        config: AuditRetentionWorkerConfig | None = None,
        clock: Clock | None = None,
        wait_for_stop: WaitForStop | None = None,
    ) -> None:
        self._validate_worker_id(worker_id)
        self._service = service
        self._worker_id = worker_id
        self._config = config or AuditRetentionWorkerConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._wait_for_stop = wait_for_stop or self._default_wait_for_stop
        self._stop_event = asyncio.Event()
        self._started = False
        self._running = False
        self._state = AuditRetentionWorkerState.STOPPED
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._last_cycle_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self._consecutive_failures = 0
        self._total_cycles = 0
        self._total_purged_workflow_runs = 0

    @property
    def health(self) -> AuditRetentionWorkerHealth:
        """返回不会被调用方修改的清理健康快照。"""
        return AuditRetentionWorkerHealth(
            state=self._state,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            last_cycle_at=self._last_cycle_at,
            last_success_at=self._last_success_at,
            last_error=self._last_error,
            consecutive_failures=self._consecutive_failures,
            total_cycles=self._total_cycles,
            total_purged_workflow_runs=self._total_purged_workflow_runs,
        )

    async def run(self) -> None:
        """运行单个清理循环；同一实例禁止重入和重启。"""
        if self._running:
            raise RuntimeError("Audit retention worker is already running")
        if self._started:
            raise RuntimeError(
                "Audit retention worker instance cannot be restarted"
            )
        self._started = True
        self._running = True
        self._state = AuditRetentionWorkerState.RUNNING
        self._started_at = self._now()
        self._stopped_at = None
        try:
            while not self._stop_event.is_set():
                delay = await self._run_cycle()
                if self._stop_event.is_set():
                    break
                await self._wait_for_stop(
                    self._stop_event,
                    delay.total_seconds(),
                )
        except asyncio.CancelledError:
            self._state = AuditRetentionWorkerState.STOPPING
            raise
        finally:
            self._running = False
            self._state = AuditRetentionWorkerState.STOPPED
            self._stopped_at = self._now()

    def request_stop(self) -> None:
        """请求协作式停机，不中断当前有界数据库事务。"""
        if self._running:
            self._state = AuditRetentionWorkerState.STOPPING
        self._stop_event.set()

    async def _run_cycle(self) -> timedelta:
        """执行一批清理，并按结果选择下一等待时间。"""
        self._total_cycles += 1
        try:
            result = await self._service.purge_once()
        except Exception as exc:
            self._last_cycle_at = self._now()
            self._consecutive_failures += 1
            self._state = AuditRetentionWorkerState.DEGRADED
            self._last_error = self._safe_error_message(exc)
            logger.error(
                "审计留存清理批次失败",
                extra={
                    "worker_id": self._worker_id,
                    "consecutive_failures": self._consecutive_failures,
                    "error": self._last_error,
                },
            )
            return self._error_backoff_delay()

        now = self._now()
        self._last_cycle_at = now
        self._last_success_at = now
        self._last_error = None
        self._consecutive_failures = 0
        self._state = AuditRetentionWorkerState.RUNNING
        self._total_purged_workflow_runs += result.purged_workflow_runs
        if result.purged_workflow_runs == self._config.batch_size:
            return self._config.active_interval
        return self._config.idle_interval

    def _error_backoff_delay(self) -> timedelta:
        """计算带 Worker 稳定抖动的指数异常退避。"""
        seconds = self._config.error_backoff_initial.total_seconds()
        maximum = self._config.error_backoff_max.total_seconds()
        remaining_steps = self._consecutive_failures - 1
        while remaining_steps > 0 and seconds < maximum:
            seconds = min(seconds * 2, maximum)
            remaining_steps -= 1
        digest = sha256(
            f"{self._worker_id}:error:{self._consecutive_failures}".encode()
        ).digest()
        ratio = int.from_bytes(digest[:8], "big") / ((2**64) - 1)
        return timedelta(seconds=seconds * (0.5 + ratio * 0.5))

    def _now(self) -> datetime:
        """读取带时区时钟。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    async def _default_wait_for_stop(
        stop_event: asyncio.Event,
        timeout_seconds: float,
    ) -> bool:
        """等待停止信号或下一轮调度时间。"""
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return False
        return True

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        """生成有限长度单行错误摘要。"""
        detail = " ".join(str(exc).splitlines()).strip()
        detail = redact_sensitive_text(detail)[0]
        message = type(exc).__name__
        if detail:
            message = f"{message}: {detail}"
        return message[:2048]

    @staticmethod
    def _validate_worker_id(worker_id: str) -> None:
        """校验 Worker 身份，避免污染日志和抖动种子。"""
        validate_worker_id(worker_id)
