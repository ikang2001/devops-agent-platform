import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol

from devops_agent_platform.application.services.outbox_dispatcher import (
    OutboxDispatchResult,
)
from devops_agent_platform.domain.enums import OutboxWorkerState
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.tools.sanitization import redact_sensitive_text

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
WaitForStop = Callable[[asyncio.Event, float], Awaitable[bool]]


class OutboxDispatcherPort(Protocol):
    """Runner依赖的单批次发布能力。"""

    async def run_once(self) -> OutboxDispatchResult:
        """处理一个有界批次。"""
        ...


@dataclass(frozen=True)
class OutboxWorkerConfig:
    """Worker轮询和退避配置。"""

    poll_interval: timedelta = timedelta(milliseconds=200)
    empty_backoff_initial: timedelta = timedelta(seconds=1)
    empty_backoff_max: timedelta = timedelta(seconds=30)
    error_backoff_initial: timedelta = timedelta(seconds=1)
    error_backoff_max: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        """校验退避时间为正数且初始值不超过上限。"""
        durations = {
            "poll_interval": self.poll_interval,
            "empty_backoff_initial": self.empty_backoff_initial,
            "empty_backoff_max": self.empty_backoff_max,
            "error_backoff_initial": self.error_backoff_initial,
            "error_backoff_max": self.error_backoff_max,
        }
        for field_name, value in durations.items():
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise AppValidationError(f"{field_name} must be positive")
        if self.empty_backoff_initial > self.empty_backoff_max:
            raise AppValidationError(
                "empty_backoff_initial must not exceed empty_backoff_max"
            )
        if self.error_backoff_initial > self.error_backoff_max:
            raise AppValidationError(
                "error_backoff_initial must not exceed error_backoff_max"
            )


@dataclass(frozen=True)
class OutboxWorkerHealth:
    """供健康检查和监控采集使用的不可变快照。"""

    state: OutboxWorkerState
    started_at: datetime | None
    stopped_at: datetime | None
    last_cycle_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    total_claimed: int
    total_published: int
    total_retried: int
    total_failed: int


class OutboxWorkerRunner:
    """持续调用Dispatcher并管理退避、健康状态和协作式停机。"""

    def __init__(
        self,
        dispatcher: OutboxDispatcherPort,
        worker_id: str,
        config: OutboxWorkerConfig | None = None,
        clock: Clock | None = None,
        wait_for_stop: WaitForStop | None = None,
    ) -> None:
        self._validate_worker_id(worker_id)
        self._dispatcher = dispatcher
        self._worker_id = worker_id
        self._config = config or OutboxWorkerConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._wait_for_stop = wait_for_stop or self._default_wait_for_stop
        self._stop_event = asyncio.Event()
        self._started = False
        self._running = False
        self._state = OutboxWorkerState.STOPPED
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._last_cycle_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self._consecutive_failures = 0
        self._empty_cycles = 0
        self._total_claimed = 0
        self._total_published = 0
        self._total_retried = 0
        self._total_failed = 0

    @property
    def health(self) -> OutboxWorkerHealth:
        """返回不会被调用方修改的Worker健康快照。"""
        return OutboxWorkerHealth(
            state=self._state,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            last_cycle_at=self._last_cycle_at,
            last_success_at=self._last_success_at,
            last_error=self._last_error,
            consecutive_failures=self._consecutive_failures,
            total_claimed=self._total_claimed,
            total_published=self._total_published,
            total_retried=self._total_retried,
            total_failed=self._total_failed,
        )

    async def run(self) -> None:
        """运行单个Worker循环；同一实例禁止重入和重启。"""
        if self._running:
            raise RuntimeError("Outbox worker is already running")
        if self._started:
            raise RuntimeError("Outbox worker instance cannot be restarted")

        self._started = True
        self._running = True
        self._state = OutboxWorkerState.RUNNING
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
            self._state = OutboxWorkerState.STOPPING
            raise
        finally:
            self._running = False
            self._state = OutboxWorkerState.STOPPED
            self._stopped_at = self._now()

    def request_stop(self) -> None:
        """请求协作式停机，不中断正在处理的有界批次。"""
        if self._running:
            self._state = OutboxWorkerState.STOPPING
        self._stop_event.set()

    async def _run_cycle(self) -> timedelta:
        """执行一轮并根据结果选择下一次等待时间。"""
        try:
            result = await self._dispatcher.run_once()
        except Exception as exc:
            self._last_cycle_at = self._now()
            self._consecutive_failures += 1
            self._state = OutboxWorkerState.DEGRADED
            self._last_error = self._safe_error_message(exc)
            logger.error(
                "Outbox Worker执行批次失败",
                extra={
                    "worker_id": self._worker_id,
                    "consecutive_failures": self._consecutive_failures,
                },
            )
            return self._backoff_delay(
                initial=self._config.error_backoff_initial,
                maximum=self._config.error_backoff_max,
                streak=self._consecutive_failures,
                category="error",
            )

        now = self._now()
        self._last_cycle_at = now
        self._last_success_at = now
        self._last_error = None
        self._consecutive_failures = 0
        self._state = OutboxWorkerState.RUNNING
        self._total_claimed += result.claimed
        self._total_published += result.published
        self._total_retried += result.retried
        self._total_failed += result.failed

        if result.claimed > 0:
            self._empty_cycles = 0
            return self._config.poll_interval

        self._empty_cycles += 1
        return self._backoff_delay(
            initial=self._config.empty_backoff_initial,
            maximum=self._config.empty_backoff_max,
            streak=self._empty_cycles,
            category="empty",
        )

    def _backoff_delay(
        self,
        initial: timedelta,
        maximum: timedelta,
        streak: int,
        category: str,
    ) -> timedelta:
        """计算带Worker稳定抖动的指数退避。"""
        capped_seconds = initial.total_seconds()
        maximum_seconds = maximum.total_seconds()
        remaining_steps = streak - 1
        while remaining_steps > 0 and capped_seconds < maximum_seconds:
            capped_seconds = min(capped_seconds * 2, maximum_seconds)
            remaining_steps -= 1
        digest = sha256(
            f"{self._worker_id}:{category}:{streak}".encode()
        ).digest()
        ratio = int.from_bytes(digest[:8], "big") / ((2**64) - 1)
        return timedelta(seconds=capped_seconds * (0.5 + ratio * 0.5))

    def _now(self) -> datetime:
        """读取带时区时钟。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return timezone-aware datetime")
        return value

    @staticmethod
    async def _default_wait_for_stop(
        stop_event: asyncio.Event,
        timeout_seconds: float,
    ) -> bool:
        """等待停止信号或轮询间隔结束。"""
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
        """生成有限长度、已脱敏的单行错误摘要。"""
        detail = " ".join(str(exc).splitlines()).strip()
        detail = redact_sensitive_text(detail)[0]
        message = type(exc).__name__
        if detail:
            message = f"{message}: {detail}"
        return message[:2048]

    @staticmethod
    def _validate_worker_id(worker_id: str) -> None:
        """校验Worker身份，防止健康日志字段污染。"""
        validate_worker_id(worker_id)
