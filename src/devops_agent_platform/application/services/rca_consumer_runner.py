import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol

from devops_agent_platform.domain.enums import RCAConsumerWorkerState
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.ports.messaging import MessageConsumeResult
from devops_agent_platform.tools.sanitization import redact_sensitive_text

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
WaitForStop = Callable[[asyncio.Event, float], Awaitable[bool]]


class RCAConsumerPort(Protocol):
    """Runner依赖的Kafka Consumer生命周期和单次轮询能力。"""

    async def start(self) -> None:
        """启动Consumer及死信通道。"""
        ...

    async def run_once(self) -> MessageConsumeResult:
        """处理至多一条消息。"""
        ...

    async def close(self) -> None:
        """关闭Consumer及死信通道。"""
        ...


@dataclass(frozen=True)
class RCAConsumerRunnerConfig:
    """RCA消费循环的活跃间隔和分类退避配置。"""

    active_poll_interval: timedelta = timedelta(milliseconds=50)
    empty_backoff_initial: timedelta = timedelta(milliseconds=200)
    empty_backoff_max: timedelta = timedelta(seconds=5)
    retry_backoff_initial: timedelta = timedelta(seconds=1)
    retry_backoff_max: timedelta = timedelta(seconds=30)
    error_backoff_initial: timedelta = timedelta(seconds=1)
    error_backoff_max: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        """校验全部持续时间及初始值、上限关系。"""
        durations = {
            "active_poll_interval": self.active_poll_interval,
            "empty_backoff_initial": self.empty_backoff_initial,
            "empty_backoff_max": self.empty_backoff_max,
            "retry_backoff_initial": self.retry_backoff_initial,
            "retry_backoff_max": self.retry_backoff_max,
            "error_backoff_initial": self.error_backoff_initial,
            "error_backoff_max": self.error_backoff_max,
        }
        for field_name, value in durations.items():
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise AppValidationError(f"{field_name} must be positive")
        pairs = (
            (
                "empty_backoff_initial",
                self.empty_backoff_initial,
                self.empty_backoff_max,
            ),
            (
                "retry_backoff_initial",
                self.retry_backoff_initial,
                self.retry_backoff_max,
            ),
            (
                "error_backoff_initial",
                self.error_backoff_initial,
                self.error_backoff_max,
            ),
        )
        for field_name, initial, maximum in pairs:
            if initial > maximum:
                raise AppValidationError(
                    f"{field_name} must not exceed its maximum"
                )


@dataclass(frozen=True)
class RCAConsumerHealth:
    """供就绪检查和监控读取的不可变消费健康快照。"""

    state: RCAConsumerWorkerState
    started_at: datetime | None
    stopped_at: datetime | None
    last_cycle_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    total_polled: int
    total_acknowledged: int
    total_retried: int
    total_dead_lettered: int
    lag_source_up: bool = False
    assigned_partitions: int = 0
    measured_partitions: int = 0
    total_lag: int = 0
    max_partition_lag: int = 0


class RCAConsumerRunner:
    """管理RCA Consumer持续轮询、退避、健康和协作式停机。"""

    def __init__(
        self,
        consumer: RCAConsumerPort,
        worker_id: str,
        config: RCAConsumerRunnerConfig | None = None,
        clock: Clock | None = None,
        wait_for_stop: WaitForStop | None = None,
    ) -> None:
        self._validate_worker_id(worker_id)
        self._consumer = consumer
        self._worker_id = worker_id
        self._config = config or RCAConsumerRunnerConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._wait_for_stop = wait_for_stop or self._default_wait_for_stop
        self._stop_event = asyncio.Event()
        self._started = False
        self._running = False
        self._state = RCAConsumerWorkerState.STOPPED
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._last_cycle_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self._consecutive_failures = 0
        self._empty_cycles = 0
        self._retry_cycles = 0
        self._total_polled = 0
        self._total_acknowledged = 0
        self._total_retried = 0
        self._total_dead_lettered = 0
        self._lag_source_up = False
        self._assigned_partitions = 0
        self._measured_partitions = 0
        self._total_lag = 0
        self._max_partition_lag = 0

    @property
    def health(self) -> RCAConsumerHealth:
        """返回不会被调用方修改的消费健康快照。"""
        return RCAConsumerHealth(
            state=self._state,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            last_cycle_at=self._last_cycle_at,
            last_success_at=self._last_success_at,
            last_error=self._last_error,
            consecutive_failures=self._consecutive_failures,
            total_polled=self._total_polled,
            total_acknowledged=self._total_acknowledged,
            total_retried=self._total_retried,
            total_dead_lettered=self._total_dead_lettered,
            lag_source_up=self._lag_source_up,
            assigned_partitions=self._assigned_partitions,
            measured_partitions=self._measured_partitions,
            total_lag=self._total_lag,
            max_partition_lag=self._max_partition_lag,
        )

    async def run(self) -> None:
        """启动Consumer并循环轮询；同一实例禁止重入和重启。"""
        if self._running:
            raise RuntimeError("RCA consumer runner is already running")
        if self._started:
            raise RuntimeError("RCA consumer runner cannot be restarted")

        self._started = True
        self._running = True
        self._state = RCAConsumerWorkerState.RUNNING
        self._started_at = self._now()
        self._stopped_at = None
        try:
            await self._consumer.start()
            while not self._stop_event.is_set():
                delay = await self._run_cycle()
                if self._stop_event.is_set():
                    break
                await self._wait_for_stop(
                    self._stop_event,
                    delay.total_seconds(),
                )
        except asyncio.CancelledError:
            self._state = RCAConsumerWorkerState.STOPPING
            raise
        finally:
            try:
                await self._consumer.close()
            finally:
                self._running = False
                self._state = RCAConsumerWorkerState.STOPPED
                self._stopped_at = self._now()

    def request_stop(self) -> None:
        """请求协作式停机，不取消正在处理的单条消息。"""
        if self._running:
            self._state = RCAConsumerWorkerState.STOPPING
        self._stop_event.set()

    async def _run_cycle(self) -> timedelta:
        """执行一轮消费并按结果选择下一等待时间。"""
        try:
            result = await self._consumer.run_once()
        except Exception as exc:
            self._last_cycle_at = self._now()
            self._consecutive_failures += 1
            self._state = RCAConsumerWorkerState.DEGRADED
            self._last_error = self._safe_error_message(exc)
            logger.error(
                "RCA Consumer执行轮询失败",
                extra={
                    "worker_id": self._worker_id,
                    "consecutive_failures": self._consecutive_failures,
                },
            )
            return self._backoff_delay(
                self._config.error_backoff_initial,
                self._config.error_backoff_max,
                self._consecutive_failures,
                "error",
            )

        now = self._now()
        self._last_cycle_at = now
        self._total_polled += result.polled
        self._total_acknowledged += result.acknowledged
        self._total_retried += result.retried
        self._total_dead_lettered += result.dead_lettered
        if result.lag_snapshot is not None:
            self._lag_source_up = result.lag_snapshot.source_up
            self._assigned_partitions = (
                result.lag_snapshot.assigned_partitions
            )
            self._measured_partitions = (
                result.lag_snapshot.measured_partitions
            )
            self._total_lag = result.lag_snapshot.total_lag
            self._max_partition_lag = (
                result.lag_snapshot.max_partition_lag
            )

        if result.retried > 0:
            self._retry_cycles += 1
            self._empty_cycles = 0
            self._consecutive_failures += 1
            self._state = RCAConsumerWorkerState.DEGRADED
            self._last_error = self._safe_retry_reason(
                result.retry_reason,
                "Kafka message requires retry",
            )
            return self._backoff_delay(
                self._config.retry_backoff_initial,
                self._config.retry_backoff_max,
                self._retry_cycles,
                "retry",
            )

        self._last_success_at = now
        self._last_error = None
        self._consecutive_failures = 0
        self._retry_cycles = 0
        self._state = RCAConsumerWorkerState.RUNNING
        if result.polled > 0:
            self._empty_cycles = 0
            return self._config.active_poll_interval

        self._empty_cycles += 1
        return self._backoff_delay(
            self._config.empty_backoff_initial,
            self._config.empty_backoff_max,
            self._empty_cycles,
            "empty",
        )

    def _backoff_delay(
        self,
        initial: timedelta,
        maximum: timedelta,
        streak: int,
        category: str,
    ) -> timedelta:
        """计算带Worker稳定抖动且有上限的指数退避。"""
        seconds = initial.total_seconds()
        maximum_seconds = maximum.total_seconds()
        remaining_steps = streak - 1
        while remaining_steps > 0 and seconds < maximum_seconds:
            seconds = min(seconds * 2, maximum_seconds)
            remaining_steps -= 1
        digest = sha256(
            f"{self._worker_id}:{category}:{streak}".encode()
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
        return value

    @staticmethod
    async def _default_wait_for_stop(
        stop_event: asyncio.Event,
        timeout_seconds: float,
    ) -> bool:
        """等待停止信号或退避时间结束。"""
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
    def _safe_retry_reason(reason: str | None, fallback: str) -> str:
        """清洗重试原因，防止Processor变化时把敏感文本写入健康快照。"""
        detail = " ".join(str(reason or fallback).splitlines()).strip()
        detail = redact_sensitive_text(detail)[0]
        return (detail or fallback)[:2048]

    @staticmethod
    def _validate_worker_id(worker_id: str) -> None:
        """校验Worker标识，避免日志和消费租约字段污染。"""
        validate_worker_id(worker_id)
