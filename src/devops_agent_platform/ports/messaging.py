from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class DeadLetterRecord:
    """需要可靠写入死信Topic的原始消费记录和失败原因。"""

    source_topic: str
    source_partition: int
    source_offset: int
    key: bytes | None
    value: bytes
    headers: tuple[tuple[str, bytes], ...]
    reason_code: str
    reason: str
    failed_at: datetime

    def __post_init__(self) -> None:
        """校验死信索引、原始字节和失败时间，避免二次发布失败。"""
        self._validate_text("source_topic", self.source_topic, 249)
        self._validate_non_negative_int(
            "source_partition",
            self.source_partition,
        )
        self._validate_non_negative_int("source_offset", self.source_offset)
        if self.key is not None and not isinstance(self.key, bytes):
            raise AppValidationError("dead-letter key must be bytes or None")
        if not isinstance(self.value, bytes):
            raise AppValidationError("dead-letter value must be bytes")
        if not isinstance(self.headers, tuple):
            raise AppValidationError("dead-letter headers are invalid")
        for name, value in self.headers:
            if not isinstance(value, bytes):
                raise AppValidationError("dead-letter headers are invalid")
            self._validate_text("dead-letter header name", name, 256)
        self._validate_text("reason_code", self.reason_code, 128)
        self._validate_text("reason", self.reason, 1024)
        if (
            not isinstance(self.failed_at, datetime)
            or self.failed_at.tzinfo is None
            or self.failed_at.utcoffset() is None
        ):
            raise AppValidationError(
                "dead-letter failed_at must include timezone information"
            )

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {maximum}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )

    @staticmethod
    def _validate_non_negative_int(field_name: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AppValidationError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True)
class ConsumerLagSnapshot:
    """单个Consumer实例当前分配分区的低基数Lag汇总。"""

    source_up: bool
    assigned_partitions: int
    measured_partitions: int
    total_lag: int
    max_partition_lag: int

    def __post_init__(self) -> None:
        """校验分区计数和Lag汇总关系。"""
        if not isinstance(self.source_up, bool):
            raise AppValidationError("lag source_up must be a boolean")
        for field_name, value in (
            ("assigned_partitions", self.assigned_partitions),
            ("measured_partitions", self.measured_partitions),
            ("total_lag", self.total_lag),
            ("max_partition_lag", self.max_partition_lag),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AppValidationError(f"{field_name} must be a non-negative integer")
        if self.measured_partitions > self.assigned_partitions:
            raise AppValidationError(
                "measured_partitions must not exceed assigned_partitions"
            )
        if self.max_partition_lag > self.total_lag:
            raise AppValidationError("max_partition_lag must not exceed total_lag")
        if self.measured_partitions == 0 and (
            self.total_lag != 0 or self.max_partition_lag != 0
        ):
            raise AppValidationError("unmeasured lag snapshot must contain zero lag")
        if self.source_up and (
            self.assigned_partitions == 0
            or self.measured_partitions != self.assigned_partitions
        ):
            raise AppValidationError(
                "healthy lag snapshot requires all assigned partitions"
            )


@dataclass(frozen=True)
class MessageConsumeResult:
    """单次有界消息轮询的中间件无关统计。"""

    polled: int
    acknowledged: int
    retried: int
    dead_lettered: int
    retry_reason: str | None = None
    lag_snapshot: ConsumerLagSnapshot | None = None


class DeadLetterPublisherPort(Protocol):
    """死信发布器及其进程级生命周期端口。"""

    async def start(self) -> None:
        """初始化死信发布连接。"""
        ...

    async def publish(self, record: DeadLetterRecord) -> None:
        """发布死信并等待Broker确认。"""
        ...

    async def close(self) -> None:
        """刷新并关闭发布连接。"""
        ...
