import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from devops_agent_platform.application.events import ClaimedOutboxEvent
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.ports.events import EventPublisherPort
from devops_agent_platform.ports.outbox import OutboxDispatchStorePort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class OutboxDispatcherConfig:
    """Outbox 发布批次、租约、超时和退避配置。"""

    batch_size: int = 20
    max_attempts: int = 5
    lease_duration: timedelta = timedelta(seconds=120)
    publish_timeout: timedelta = timedelta(seconds=5)
    base_backoff: timedelta = timedelta(seconds=1)
    max_backoff: timedelta = timedelta(seconds=60)

    def __post_init__(self) -> None:
        """校验配置组合，阻止批次处理时间超过租约。"""
        self._validate_positive_int("batch_size", self.batch_size, 100)
        self._validate_positive_int("max_attempts", self.max_attempts, 100)
        self._validate_positive_duration("lease_duration", self.lease_duration)
        self._validate_positive_duration("publish_timeout", self.publish_timeout)
        self._validate_positive_duration("base_backoff", self.base_backoff)
        self._validate_positive_duration("max_backoff", self.max_backoff)
        if self.base_backoff > self.max_backoff:
            raise AppValidationError(
                "base_backoff must not be greater than max_backoff"
            )
        required_lease = self.publish_timeout * self.batch_size
        if self.lease_duration <= required_lease:
            raise AppValidationError(
                "lease_duration must exceed batch_size multiplied by publish_timeout"
            )

    @staticmethod
    def _validate_positive_int(
        field_name: str,
        value: int,
        maximum: int,
    ) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise AppValidationError(f"{field_name} must be an integer")
        if not 1 <= value <= maximum:
            raise AppValidationError(f"{field_name} must be between 1 and {maximum}")

    @staticmethod
    def _validate_positive_duration(
        field_name: str,
        value: timedelta,
    ) -> None:
        if not isinstance(value, timedelta) or value <= timedelta(0):
            raise AppValidationError(f"{field_name} must be positive")


@dataclass(frozen=True)
class OutboxDispatchResult:
    """单次轮询的处理计数，供日志和监控指标使用。"""

    claimed: int
    published: int
    retried: int
    failed: int


class OutboxDispatcher:
    """抢占并发布一批 Outbox 事件。"""

    def __init__(
        self,
        store: OutboxDispatchStorePort,
        publisher: EventPublisherPort,
        worker_id: str,
        config: OutboxDispatcherConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._validate_worker_id(worker_id)
        self._store = store
        self._publisher = publisher
        self._worker_id = worker_id
        self._config = config or OutboxDispatcherConfig()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run_once(self) -> OutboxDispatchResult:
        """抢占并顺序处理一个有界批次。"""
        claim_time = self._now()
        claimed_events = await self._store.claim_batch(
            worker_id=self._worker_id,
            now=claim_time,
            locked_until=claim_time + self._config.lease_duration,
            limit=self._config.batch_size,
            max_attempts=self._config.max_attempts,
        )

        published = 0
        retried = 0
        failed = 0
        for claimed in claimed_events:
            disposition = await self._dispatch_one(claimed)
            if disposition == "PUBLISHED":
                published += 1
            elif disposition == "RETRY":
                retried += 1
            else:
                failed += 1

        return OutboxDispatchResult(
            claimed=len(claimed_events),
            published=published,
            retried=retried,
            failed=failed,
        )

    async def _dispatch_one(self, claimed: ClaimedOutboxEvent) -> str:
        """发布单条事件并完成对应状态流转。"""
        try:
            async with asyncio.timeout(self._config.publish_timeout.total_seconds()):
                await self._publisher.publish(claimed.event)
        except Exception as exc:
            error_message = self._safe_error_message(exc)
            if claimed.attempts >= self._config.max_attempts:
                await self._store.mark_failed(
                    event_id=claimed.event.event_id,
                    worker_id=self._worker_id,
                    last_error=error_message,
                )
                return "FAILED"

            delay = self._retry_delay(
                event_id=claimed.event.event_id,
                attempts=claimed.attempts,
            )
            await self._store.mark_retry(
                event_id=claimed.event.event_id,
                worker_id=self._worker_id,
                available_at=self._now() + delay,
                last_error=error_message,
            )
            return "RETRY"

        await self._store.mark_published(
            event_id=claimed.event.event_id,
            worker_id=self._worker_id,
            published_at=self._now(),
        )
        return "PUBLISHED"

    def _retry_delay(self, event_id: str, attempts: int) -> timedelta:
        """计算带确定性抖动的指数退避时间。"""
        exponential_seconds = self._config.base_backoff.total_seconds() * (
            2 ** (attempts - 1)
        )
        capped_seconds = min(
            exponential_seconds,
            self._config.max_backoff.total_seconds(),
        )
        digest = sha256(f"{event_id}:{attempts}".encode()).digest()
        ratio = int.from_bytes(digest[:8], "big") / ((2**64) - 1)
        jittered_seconds = capped_seconds * (0.5 + ratio * 0.5)
        return timedelta(seconds=jittered_seconds)

    def _now(self) -> datetime:
        """读取带时区时钟，防止测试或配置注入无时区时间。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return timezone-aware datetime")
        return value

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        """生成单行、有限长度、已脱敏的错误摘要。"""
        detail = "".join(
            " " if ord(character) < 32 or ord(character) == 127 else character
            for character in str(exc)
        )
        detail = " ".join(detail.split()).strip()
        detail = redact_sensitive_text(detail)[0]
        message = type(exc).__name__
        if detail:
            message = f"{message}: {detail}"
        return message[:2048]

    @staticmethod
    def _validate_worker_id(worker_id: str) -> None:
        """校验用于租约所有权判断的Worker标识。"""
        validate_worker_id(worker_id)
