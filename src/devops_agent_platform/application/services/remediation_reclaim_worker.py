import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol

from devops_agent_platform.domain.enums import RemediationReclaimWorkerState
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.tools.sanitization import redact_sensitive_text

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
WaitForStop = Callable[[asyncio.Event, float], Awaitable[bool]]


class RemediationReclaimServicePort(Protocol):
    """过期修复租约回收 Runner 所需的单批应用能力。"""

    async def reclaim_stale(self, *, limit: int = 50) -> Collection[object]:
        """回收一个有界批次的过期执行或回滚租约。"""
        ...


@dataclass(frozen=True)
class RemediationReclaimWorkerConfig:
    """修复租约回收 Worker 的轮询、退避和批量策略。"""

    interval: timedelta = timedelta(seconds=30)
    error_backoff_initial: timedelta = timedelta(seconds=5)
    error_backoff_max: timedelta = timedelta(minutes=5)
    batch_size: int = 50

    def __post_init__(self) -> None:
        durations = {
            "interval": self.interval,
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
class RemediationReclaimWorkerHealth:
    """供 readiness 和低基数指标读取的不可变健康快照。"""

    state: RemediationReclaimWorkerState
    started_at: datetime | None
    stopped_at: datetime | None
    last_cycle_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    total_cycles: int
    total_reclaimed_plans: int


class RemediationReclaimWorkerRunner:
    """定期收口过期租约，不重放或接管任何外部修复写操作。"""

    def __init__(
        self,
        service: RemediationReclaimServicePort,
        worker_id: str,
        config: RemediationReclaimWorkerConfig | None = None,
        clock: Clock | None = None,
        wait_for_stop: WaitForStop | None = None,
    ) -> None:
        validate_worker_id(worker_id)
        self._service = service
        self._worker_id = worker_id
        self._config = config or RemediationReclaimWorkerConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._wait_for_stop = wait_for_stop or self._default_wait_for_stop
        self._stop_event = asyncio.Event()
        self._started = False
        self._running = False
        self._state = RemediationReclaimWorkerState.STOPPED
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._last_cycle_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self._consecutive_failures = 0
        self._total_cycles = 0
        self._total_reclaimed_plans = 0

    @property
    def health(self) -> RemediationReclaimWorkerHealth:
        return RemediationReclaimWorkerHealth(
            state=self._state,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            last_cycle_at=self._last_cycle_at,
            last_success_at=self._last_success_at,
            last_error=self._last_error,
            consecutive_failures=self._consecutive_failures,
            total_cycles=self._total_cycles,
            total_reclaimed_plans=self._total_reclaimed_plans,
        )

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("Remediation reclaim worker is already running")
        if self._started:
            raise RuntimeError(
                "Remediation reclaim worker instance cannot be restarted"
            )
        self._started = True
        self._running = True
        self._state = RemediationReclaimWorkerState.RUNNING
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
            self._state = RemediationReclaimWorkerState.STOPPING
            raise
        finally:
            self._running = False
            self._state = RemediationReclaimWorkerState.STOPPED
            self._stopped_at = self._now()

    def request_stop(self) -> None:
        """请求协作式停止，不中断当前有界数据库事务。"""
        if self._running:
            self._state = RemediationReclaimWorkerState.STOPPING
        self._stop_event.set()

    async def _run_cycle(self) -> timedelta:
        self._total_cycles += 1
        try:
            reclaimed = await self._service.reclaim_stale(limit=self._config.batch_size)
        except Exception as exc:
            self._last_cycle_at = self._now()
            self._consecutive_failures += 1
            self._state = RemediationReclaimWorkerState.DEGRADED
            self._last_error = self._safe_error_message(exc)
            logger.error(
                "修复执行租约回收批次失败",
                extra={
                    "worker_id": self._worker_id,
                    "consecutive_failures": self._consecutive_failures,
                },
            )
            return self._error_backoff_delay()

        now = self._now()
        self._last_cycle_at = now
        self._last_success_at = now
        self._last_error = None
        self._consecutive_failures = 0
        self._state = RemediationReclaimWorkerState.RUNNING
        self._total_reclaimed_plans += len(reclaimed)
        return self._config.interval

    def _error_backoff_delay(self) -> timedelta:
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
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return False
        return True

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        detail = " ".join(str(exc).splitlines()).strip()
        detail = redact_sensitive_text(detail)[0]
        message = type(exc).__name__
        if detail:
            message = f"{message}: {detail}"
        return message[:2048]
