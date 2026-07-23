import asyncio
import logging
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from devops_agent_platform.application.exceptions import MessageConsumeError
from devops_agent_platform.application.services import (
    ticket_submission_record_processor as ticket_submission_processor,
)
from devops_agent_platform.application.services.rca_record_processor import (
    MessageDisposition,
    MessageProcessingResult,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.messaging import (
    ConsumerLagSnapshot,
    DeadLetterPublisherPort,
    DeadLetterRecord,
    MessageConsumeResult,
)

logger = logging.getLogger(__name__)
_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,248}$")
_SECURITY_PROTOCOLS = frozenset({"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"})
_SASL_MECHANISMS = frozenset({"PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"})


class ConsumerRecordProtocol(Protocol):
    """单次消费实际读取的Kafka记录字段。"""

    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes
    headers: list[tuple[str, bytes]]


class KafkaConsumerProtocol(Protocol):
    """RCA消费者使用的AIOKafkaConsumer最小接口。"""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def getmany(
        self,
        *,
        timeout_ms: int,
        max_records: int,
    ) -> Mapping[TopicPartition, list[ConsumerRecordProtocol]]: ...

    async def commit(
        self,
        offsets: Mapping[TopicPartition, OffsetAndMetadata],
    ) -> None: ...

    def seek(self, partition: TopicPartition, offset: int) -> None: ...

    def assignment(self) -> set[TopicPartition]: ...

    async def position(self, partition: TopicPartition) -> int: ...

    def highwater(self, partition: TopicPartition) -> int | None: ...


ConsumerFactory = Callable[..., KafkaConsumerProtocol]
TicketSubmissionDisposition = (
    ticket_submission_processor.TicketSubmissionMessageDisposition
)
TicketSubmissionProcessingResult = (
    ticket_submission_processor.TicketSubmissionProcessingResult
)


class RecordProcessorPort(Protocol):
    """Kafka适配器依赖的最小原始记录处理能力。"""

    async def process(self, value: bytes) -> MessageProcessingResult:
        """返回offset处置决定。"""
        ...


class TicketSubmissionRecordProcessorPort(Protocol):
    """工单提交Kafka适配器依赖的最小原始记录处理能力。"""

    async def process(self, value: bytes) -> TicketSubmissionProcessingResult:
        """返回工单提交消息的offset处置决定。"""
        ...


@dataclass(frozen=True)
class KafkaConsumerConfig:
    """手动提交Kafka消费者连接、消费组和轮询边界配置。"""

    bootstrap_servers: tuple[str, ...]
    topic: str = "devops-agent.events.v1"
    group_id: str = "devops-agent-rca-v1"
    client_id: str = "devops-agent-rca-consumer"
    security_protocol: str = "PLAINTEXT"
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None
    poll_timeout_ms: int = 1000
    request_timeout_ms: int = 10_000
    max_partition_fetch_bytes: int = 1024 * 1024
    lag_query_timeout_ms: int = 500
    max_lag_partitions: int = 1000
    lag_sample_interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        """在启动前校验地址、Topic、安全协议和数值边界。"""
        if not isinstance(self.bootstrap_servers, tuple) or not self.bootstrap_servers:
            raise AppValidationError("bootstrap_servers must be a non-empty tuple")
        for server in self.bootstrap_servers:
            self._validate_text("bootstrap_server", server, 255)
        if (
            not isinstance(self.topic, str)
            or _TOPIC_PATTERN.fullmatch(self.topic) is None
        ):
            raise AppValidationError("topic contains unsupported characters")
        self._validate_text("group_id", self.group_id, 255)
        self._validate_text("client_id", self.client_id, 128)
        if self.security_protocol not in _SECURITY_PROTOCOLS:
            raise AppValidationError("unsupported Kafka security_protocol")
        if self.security_protocol.startswith("SASL"):
            self._validate_text("sasl_mechanism", self.sasl_mechanism, 64)
            self._validate_text("sasl_username", self.sasl_username, 256)
            self._validate_text("sasl_password", self.sasl_password, 1024)
            if self.sasl_mechanism not in _SASL_MECHANISMS:
                raise AppValidationError("unsupported Kafka sasl_mechanism")
        self._validate_int("poll_timeout_ms", self.poll_timeout_ms, 1, 60_000)
        self._validate_int(
            "request_timeout_ms",
            self.request_timeout_ms,
            100,
            120_000,
        )
        self._validate_int(
            "max_partition_fetch_bytes",
            self.max_partition_fetch_bytes,
            1024,
            10 * 1024 * 1024,
        )
        self._validate_int(
            "lag_query_timeout_ms",
            self.lag_query_timeout_ms,
            10,
            5000,
        )
        self._validate_int(
            "max_lag_partitions",
            self.max_lag_partitions,
            1,
            10_000,
        )
        if (
            isinstance(self.lag_sample_interval_seconds, bool)
            or not isinstance(
                self.lag_sample_interval_seconds,
                int | float,
            )
            or not math.isfinite(float(self.lag_sample_interval_seconds))
            or not 0.1 <= self.lag_sample_interval_seconds <= 300
        ):
            raise AppValidationError(
                "lag_sample_interval_seconds must be between 0.1 and 300"
            )

    @staticmethod
    def _validate_text(
        field_name: str,
        value: object,
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
    def _validate_int(
        field_name: str,
        value: int,
        minimum: int,
        maximum: int,
    ) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise AppValidationError(f"{field_name} must be an integer")
        if not minimum <= value <= maximum:
            raise AppValidationError(
                f"{field_name} must be between {minimum} and {maximum}"
            )


KafkaConsumeResult = MessageConsumeResult


class RCAKafkaConsumer:
    """每次最多处理一条rca.requested记录的手动提交消费者。"""

    def __init__(
        self,
        config: KafkaConsumerConfig,
        processor: RecordProcessorPort,
        dead_letter_publisher: DeadLetterPublisherPort,
        consumer_factory: ConsumerFactory | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(monotonic_clock):
            raise AppValidationError("monotonic_clock must be callable")
        self._config = config
        self._processor = processor
        self._dead_letter_publisher = dead_letter_publisher
        self._consumer_factory = consumer_factory or AIOKafkaConsumer
        self._monotonic_clock = monotonic_clock
        self._consumer: KafkaConsumerProtocol | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._last_lag_snapshot: ConsumerLagSnapshot | None = None
        self._last_lag_collected_at = float("-inf")

    async def start(self) -> None:
        """先启动死信通道，再启动Consumer，失败时反向清理。"""
        async with self._lifecycle_lock:
            if self._consumer is not None:
                return
            consumer: KafkaConsumerProtocol | None = None
            dead_letter_started = False
            try:
                await self._dead_letter_publisher.start()
                dead_letter_started = True
                consumer = self._consumer_factory(
                    self._config.topic,
                    **self._consumer_options(),
                )
                await consumer.start()
            except asyncio.CancelledError:
                if consumer is not None:
                    await self._best_effort_stop(consumer)
                if dead_letter_started:
                    await self._best_effort_close_dead_letter()
                raise
            except Exception as exc:
                if consumer is not None:
                    await self._best_effort_stop(consumer)
                if dead_letter_started:
                    await self._best_effort_close_dead_letter()
                raise MessageConsumeError("Could not start Kafka consumer") from exc
            self._consumer = consumer

    async def run_once(self) -> KafkaConsumeResult:
        """轮询并处理至多一条记录。"""
        consumer = self._require_consumer()
        try:
            batches = await consumer.getmany(
                timeout_ms=self._config.poll_timeout_ms,
                max_records=1,
            )
        except Exception as exc:
            raise MessageConsumeError("Could not poll Kafka message") from exc

        selected = self._first_record(batches)
        if selected is None:
            return await self._attach_lag(
                consumer,
                KafkaConsumeResult(0, 0, 0, 0),
            )
        partition, record = selected

        try:
            result = await self._processor.process(record.value)
        except Exception as exc:
            self._seek(consumer, partition, record.offset)
            raise MessageConsumeError("Could not process Kafka message") from exc

        if result.disposition is MessageDisposition.RETRY:
            self._seek(consumer, partition, record.offset)
            return await self._attach_lag(
                consumer,
                KafkaConsumeResult(
                    1,
                    0,
                    1,
                    0,
                    retry_reason=result.reason or result.reason_code,
                ),
            )

        if result.disposition is MessageDisposition.DEAD_LETTER:
            await self._publish_dead_letter_or_rewind(
                consumer,
                partition,
                record,
                result,
            )
            await self._commit_or_rewind(consumer, partition, record.offset)
            return await self._attach_lag(
                consumer,
                KafkaConsumeResult(1, 1, 0, 1),
            )

        await self._commit_or_rewind(consumer, partition, record.offset)
        return await self._attach_lag(
            consumer,
            KafkaConsumeResult(1, 1, 0, 0),
        )

    async def close(self) -> None:
        """先停止Consumer再关闭死信Producer，重复调用安全。"""
        async with self._lifecycle_lock:
            consumer = self._consumer
            if consumer is None:
                return
            errors: list[Exception] = []
            try:
                await consumer.stop()
            except Exception as exc:
                errors.append(exc)
            try:
                await self._dead_letter_publisher.close()
            except Exception as exc:
                errors.append(exc)
            self._consumer = None
            if errors:
                raise MessageConsumeError(
                    "Could not close Kafka consumer resources"
                ) from errors[0]

    async def _publish_dead_letter_or_rewind(
        self,
        consumer: KafkaConsumerProtocol,
        partition: TopicPartition,
        record: ConsumerRecordProtocol,
        result: MessageProcessingResult,
    ) -> None:
        """发布死信失败时回退源游标，禁止丢失坏消息。"""
        try:
            await self._dead_letter_publisher.publish(
                DeadLetterRecord(
                    source_topic=record.topic,
                    source_partition=record.partition,
                    source_offset=record.offset,
                    key=record.key,
                    value=record.value,
                    headers=tuple(record.headers or ()),
                    reason_code=result.reason_code or "UNKNOWN",
                    reason=result.reason or "Unknown dead-letter reason",
                    failed_at=datetime.now(UTC),
                )
            )
        except Exception as exc:
            self._seek(consumer, partition, record.offset)
            raise MessageConsumeError("Could not publish dead-letter message") from exc

    async def _commit_or_rewind(
        self,
        consumer: KafkaConsumerProtocol,
        partition: TopicPartition,
        offset: int,
    ) -> None:
        """提交下一offset；提交失败则回退本地游标等待重试。"""
        try:
            await consumer.commit({partition: OffsetAndMetadata(offset + 1, "")})
        except Exception as exc:
            self._seek(consumer, partition, offset)
            raise MessageConsumeError("Could not commit Kafka offset") from exc

    def _consumer_options(self) -> dict[str, Any]:
        """构造手动提交、单条有界拉取的Consumer参数。"""
        options: dict[str, Any] = {
            "bootstrap_servers": list(self._config.bootstrap_servers),
            "group_id": self._config.group_id,
            "client_id": self._config.client_id,
            "enable_auto_commit": False,
            "auto_offset_reset": "earliest",
            "security_protocol": self._config.security_protocol,
            "request_timeout_ms": self._config.request_timeout_ms,
            "max_partition_fetch_bytes": (self._config.max_partition_fetch_bytes),
        }
        if self._config.security_protocol.startswith("SASL"):
            options.update(
                {
                    "sasl_mechanism": self._config.sasl_mechanism,
                    "sasl_plain_username": self._config.sasl_username,
                    "sasl_plain_password": self._config.sasl_password,
                }
            )
        return options

    async def _attach_lag(
        self,
        consumer: KafkaConsumerProtocol,
        result: KafkaConsumeResult,
    ) -> KafkaConsumeResult:
        """在不改变消息处置结果的前提下附加Lag汇总。"""
        try:
            now = self._read_monotonic()
        except Exception:
            now = None
        lag_snapshot = self._last_lag_snapshot
        if now is None:
            lag_snapshot = lag_snapshot or self._unavailable_lag(0)
        elif (
            lag_snapshot is None
            or now - self._last_lag_collected_at
            >= self._config.lag_sample_interval_seconds
        ):
            lag_snapshot = await self._collect_lag(consumer)
            self._last_lag_snapshot = lag_snapshot
            self._last_lag_collected_at = now
        return KafkaConsumeResult(
            polled=result.polled,
            acknowledged=result.acknowledged,
            retried=result.retried,
            dead_lettered=result.dead_lettered,
            retry_reason=result.retry_reason,
            lag_snapshot=lag_snapshot,
        )

    async def _collect_lag(
        self,
        consumer: KafkaConsumerProtocol,
    ) -> ConsumerLagSnapshot:
        """在短超时内汇总已分配分区Lag，失败只标记数据源不可用。"""
        assigned_count = 0
        measured_count = 0
        total_lag = 0
        max_partition_lag = 0
        try:
            async with asyncio.timeout(self._config.lag_query_timeout_ms / 1000):
                partitions = tuple(
                    sorted(
                        consumer.assignment(),
                        key=lambda item: (item.topic, item.partition),
                    )
                )
                assigned_count = len(partitions)
                if (
                    assigned_count == 0
                    or assigned_count > self._config.max_lag_partitions
                ):
                    return self._unavailable_lag(assigned_count)

                for partition in partitions:
                    highwater = consumer.highwater(partition)
                    if (
                        isinstance(highwater, bool)
                        or not isinstance(highwater, int)
                        or highwater < 0
                    ):
                        continue
                    try:
                        position = await consumer.position(partition)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
                    if (
                        isinstance(position, bool)
                        or not isinstance(position, int)
                        or position < 0
                        or position > highwater
                    ):
                        continue
                    lag = highwater - position
                    measured_count += 1
                    total_lag += lag
                    max_partition_lag = max(max_partition_lag, lag)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ConsumerLagSnapshot(
                source_up=False,
                assigned_partitions=assigned_count,
                measured_partitions=measured_count,
                total_lag=total_lag,
                max_partition_lag=max_partition_lag,
            )

        return ConsumerLagSnapshot(
            source_up=(assigned_count > 0 and measured_count == assigned_count),
            assigned_partitions=assigned_count,
            measured_partitions=measured_count,
            total_lag=total_lag,
            max_partition_lag=max_partition_lag,
        )

    @staticmethod
    def _unavailable_lag(assigned_count: int) -> ConsumerLagSnapshot:
        """构造未分配或超过容量上限时的不可用Lag快照。"""
        return ConsumerLagSnapshot(
            source_up=False,
            assigned_partitions=assigned_count,
            measured_partitions=0,
            total_lag=0,
            max_partition_lag=0,
        )

    def _read_monotonic(self) -> float:
        """读取有限单调时钟，避免系统校时影响Lag采样间隔。"""
        value = self._monotonic_clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise AppValidationError("monotonic_clock must return a finite number")
        return float(value)

    @staticmethod
    def _first_record(
        batches: Mapping[TopicPartition, list[ConsumerRecordProtocol]],
    ) -> tuple[TopicPartition, ConsumerRecordProtocol] | None:
        """确定性选择轮询结果中的第一条记录。"""
        for partition in sorted(
            batches,
            key=lambda item: (item.topic, item.partition),
        ):
            records = batches[partition]
            if records:
                return partition, records[0]
        return None

    @staticmethod
    def _seek(
        consumer: KafkaConsumerProtocol,
        partition: TopicPartition,
        offset: int,
    ) -> None:
        """回退本地消费位置；失败时报告消费基础设施异常。"""
        try:
            consumer.seek(partition, offset)
        except Exception as exc:
            raise MessageConsumeError("Could not rewind Kafka offset") from exc

    def _require_consumer(self) -> KafkaConsumerProtocol:
        """拒绝在生命周期启动前轮询。"""
        if self._consumer is None:
            raise MessageConsumeError("Kafka consumer is not started")
        return self._consumer

    @staticmethod
    async def _best_effort_stop(consumer: KafkaConsumerProtocol) -> None:
        """启动失败时尽力清理Consumer。"""
        try:
            await consumer.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "RCA Kafka Consumer启动失败后的Consumer清理失败"
            )

    async def _best_effort_close_dead_letter(self) -> None:
        """Consumer启动失败时尽力关闭已经启动的死信通道。"""
        try:
            await self._dead_letter_publisher.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "RCA Kafka Consumer启动失败后的死信通道清理失败"
            )


class TicketSubmissionKafkaConsumer:
    """每次最多处理一条ticket_submission.requested记录的手动提交消费者。"""

    def __init__(
        self,
        config: KafkaConsumerConfig,
        processor: TicketSubmissionRecordProcessorPort,
        dead_letter_publisher: DeadLetterPublisherPort,
        consumer_factory: ConsumerFactory | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(monotonic_clock):
            raise AppValidationError("monotonic_clock must be callable")
        self._config = config
        self._processor = processor
        self._dead_letter_publisher = dead_letter_publisher
        self._consumer_factory = consumer_factory or AIOKafkaConsumer
        self._monotonic_clock = monotonic_clock
        self._consumer: KafkaConsumerProtocol | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._last_lag_snapshot: ConsumerLagSnapshot | None = None
        self._last_lag_collected_at = float("-inf")

    @property
    def lag_snapshot(self) -> ConsumerLagSnapshot | None:
        """返回最近一次Lag汇总快照；尚未采集时返回None。"""
        return self._last_lag_snapshot

    async def start(self) -> None:
        """先启动死信通道，再启动Consumer，启动失败时反向清理。"""
        async with self._lifecycle_lock:
            if self._consumer is not None:
                return
            consumer: KafkaConsumerProtocol | None = None
            dead_letter_started = False
            try:
                await self._dead_letter_publisher.start()
                dead_letter_started = True
                consumer = self._consumer_factory(
                    self._config.topic,
                    **self._consumer_options(),
                )
                await consumer.start()
            except asyncio.CancelledError:
                if consumer is not None:
                    await self._best_effort_stop(consumer)
                if dead_letter_started:
                    await self._best_effort_close_dead_letter()
                raise
            except Exception as exc:
                if consumer is not None:
                    await self._best_effort_stop(consumer)
                if dead_letter_started:
                    await self._best_effort_close_dead_letter()
                raise MessageConsumeError(
                    "Could not start ticket submission Kafka consumer"
                ) from exc
            self._consumer = consumer

    async def run_once(self) -> TicketSubmissionProcessingResult | None:
        """轮询并处理至多一条记录，不在空轮询时提交offset。"""
        consumer = self._require_consumer()
        try:
            batches = await consumer.getmany(
                timeout_ms=self._config.poll_timeout_ms,
                max_records=1,
            )
        except Exception as exc:
            raise MessageConsumeError("Could not poll Kafka message") from exc

        selected = RCAKafkaConsumer._first_record(batches)
        if selected is None:
            await self._refresh_lag(consumer)
            return None
        partition, record = selected

        try:
            result = await self._processor.process(record.value)
            if not isinstance(result, TicketSubmissionProcessingResult):
                raise TypeError("process must return TicketSubmissionProcessingResult")
        except Exception as exc:
            RCAKafkaConsumer._seek(consumer, partition, record.offset)
            raise MessageConsumeError(
                "Could not process ticket submission Kafka message"
            ) from exc

        if result.disposition is TicketSubmissionDisposition.RETRY:
            RCAKafkaConsumer._seek(consumer, partition, record.offset)
            await self._refresh_lag(consumer)
            return result

        if result.disposition is TicketSubmissionDisposition.DEAD_LETTER:
            await self._publish_dead_letter_or_rewind(
                consumer,
                partition,
                record,
                result,
            )
            await self._commit_or_rewind(consumer, partition, record.offset)
            await self._refresh_lag(consumer)
            return result

        await self._commit_or_rewind(consumer, partition, record.offset)
        await self._refresh_lag(consumer)
        return result

    async def close(self) -> None:
        """先停止Consumer再关闭死信Producer，重复调用安全。"""
        async with self._lifecycle_lock:
            consumer = self._consumer
            if consumer is None:
                return
            errors: list[Exception] = []
            try:
                await consumer.stop()
            except Exception as exc:
                errors.append(exc)
            try:
                await self._dead_letter_publisher.close()
            except Exception as exc:
                errors.append(exc)
            self._consumer = None
            if errors:
                raise MessageConsumeError(
                    "Could not close ticket submission Kafka consumer resources"
                ) from errors[0]

    async def _publish_dead_letter_or_rewind(
        self,
        consumer: KafkaConsumerProtocol,
        partition: TopicPartition,
        record: ConsumerRecordProtocol,
        result: TicketSubmissionProcessingResult,
    ) -> None:
        """发布死信失败时回退源游标，禁止坏消息静默丢失。"""
        try:
            await self._dead_letter_publisher.publish(
                DeadLetterRecord(
                    source_topic=record.topic,
                    source_partition=record.partition,
                    source_offset=record.offset,
                    key=record.key,
                    value=record.value,
                    headers=tuple(record.headers or ()),
                    reason_code=result.reason_code or "UNKNOWN",
                    reason=result.reason or "Unknown dead-letter reason",
                    failed_at=datetime.now(UTC),
                )
            )
        except Exception as exc:
            RCAKafkaConsumer._seek(consumer, partition, record.offset)
            raise MessageConsumeError(
                "Could not publish ticket submission dead-letter message"
            ) from exc

    async def _commit_or_rewind(
        self,
        consumer: KafkaConsumerProtocol,
        partition: TopicPartition,
        offset: int,
    ) -> None:
        """提交下一offset；提交失败则回退本地游标等待后续重试。"""
        try:
            await consumer.commit({partition: OffsetAndMetadata(offset + 1, "")})
        except Exception as exc:
            RCAKafkaConsumer._seek(consumer, partition, offset)
            raise MessageConsumeError(
                "Could not commit ticket submission Kafka offset"
            ) from exc

    def _consumer_options(self) -> dict[str, Any]:
        """构造手动提交、单条有界拉取的Consumer参数。"""
        options: dict[str, Any] = {
            "bootstrap_servers": list(self._config.bootstrap_servers),
            "group_id": self._config.group_id,
            "client_id": self._config.client_id,
            "enable_auto_commit": False,
            "auto_offset_reset": "earliest",
            "security_protocol": self._config.security_protocol,
            "request_timeout_ms": self._config.request_timeout_ms,
            "max_partition_fetch_bytes": (self._config.max_partition_fetch_bytes),
        }
        if self._config.security_protocol.startswith("SASL"):
            options.update(
                {
                    "sasl_mechanism": self._config.sasl_mechanism,
                    "sasl_plain_username": self._config.sasl_username,
                    "sasl_plain_password": self._config.sasl_password,
                }
            )
        return options

    async def _refresh_lag(self, consumer: KafkaConsumerProtocol) -> None:
        """按配置采样间隔刷新Lag，避免每轮轮询都查询Kafka分区位置。"""
        try:
            now = self._read_monotonic()
        except Exception:
            if self._last_lag_snapshot is None:
                self._last_lag_snapshot = RCAKafkaConsumer._unavailable_lag(0)
            return
        if (
            self._last_lag_snapshot is not None
            and now - self._last_lag_collected_at
            < self._config.lag_sample_interval_seconds
        ):
            return
        self._last_lag_snapshot = await self._collect_lag(consumer)
        self._last_lag_collected_at = now

    async def _collect_lag(
        self,
        consumer: KafkaConsumerProtocol,
    ) -> ConsumerLagSnapshot:
        """在短超时内汇总已分配分区Lag，失败只标记数据源不可用。"""
        assigned_count = 0
        measured_count = 0
        total_lag = 0
        max_partition_lag = 0
        try:
            async with asyncio.timeout(self._config.lag_query_timeout_ms / 1000):
                partitions = tuple(
                    sorted(
                        consumer.assignment(),
                        key=lambda item: (item.topic, item.partition),
                    )
                )
                assigned_count = len(partitions)
                if (
                    assigned_count == 0
                    or assigned_count > self._config.max_lag_partitions
                ):
                    return RCAKafkaConsumer._unavailable_lag(assigned_count)

                for partition in partitions:
                    highwater = consumer.highwater(partition)
                    if (
                        isinstance(highwater, bool)
                        or not isinstance(highwater, int)
                        or highwater < 0
                    ):
                        continue
                    try:
                        position = await consumer.position(partition)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
                    if (
                        isinstance(position, bool)
                        or not isinstance(position, int)
                        or position < 0
                        or position > highwater
                    ):
                        continue
                    lag = highwater - position
                    measured_count += 1
                    total_lag += lag
                    max_partition_lag = max(max_partition_lag, lag)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ConsumerLagSnapshot(
                source_up=False,
                assigned_partitions=assigned_count,
                measured_partitions=measured_count,
                total_lag=total_lag,
                max_partition_lag=max_partition_lag,
            )

        return ConsumerLagSnapshot(
            source_up=(assigned_count > 0 and measured_count == assigned_count),
            assigned_partitions=assigned_count,
            measured_partitions=measured_count,
            total_lag=total_lag,
            max_partition_lag=max_partition_lag,
        )

    def _read_monotonic(self) -> float:
        """读取有限单调时钟，避免系统校时影响Lag采样间隔。"""
        value = self._monotonic_clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise AppValidationError("monotonic_clock must return a finite number")
        return float(value)

    def _require_consumer(self) -> KafkaConsumerProtocol:
        """拒绝在生命周期启动前轮询。"""
        if self._consumer is None:
            raise MessageConsumeError("Ticket submission Kafka consumer is not started")
        return self._consumer

    @staticmethod
    async def _best_effort_stop(consumer: KafkaConsumerProtocol) -> None:
        """启动失败时尽力清理Consumer。"""
        try:
            await consumer.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "工单提交Kafka Consumer启动失败后的Consumer清理失败"
            )

    async def _best_effort_close_dead_letter(self) -> None:
        """Consumer启动失败时尽力关闭已经启动的死信通道。"""
        try:
            await self._dead_letter_publisher.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "工单提交Kafka Consumer启动失败后的死信通道清理失败"
            )
