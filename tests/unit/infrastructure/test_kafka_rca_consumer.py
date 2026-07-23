import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from devops_agent_platform.application.exceptions import (
    EventPublishError,
    MessageConsumeError,
)
from devops_agent_platform.application.services.rca_record_processor import (
    MessageDisposition,
    MessageProcessingResult,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.adapters.kafka import (
    KafkaConsumerConfig,
    KafkaDeadLetterPublisher,
    KafkaPublisherConfig,
    RCAKafkaConsumer,
)
from devops_agent_platform.ports.messaging import (
    ConsumerLagSnapshot,
    DeadLetterRecord,
)

PARTITION = TopicPartition("devops-agent.events.v1", 2)
NOW = datetime(2026, 6, 28, 18, 0, tzinfo=UTC)


@dataclass
class FakeConsumerRecord:
    """测试使用的最小Kafka ConsumerRecord。"""

    topic: str = PARTITION.topic
    partition: int = PARTITION.partition
    offset: int = 7
    key: bytes | None = b"wfr_001"
    value: bytes = b'{"event_type":"rca.requested"}'
    headers: list[tuple[str, bytes]] | None = None

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = [("trace-id", b"trc_001")]


class FakeRecordProcessor:
    """返回预设消息处置或异常。"""

    def __init__(
        self,
        result: MessageProcessingResult,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.values: list[bytes] = []

    async def process(self, value: bytes) -> MessageProcessingResult:
        self.values.append(value)
        if self.error is not None:
            raise self.error
        return self.result


class FakeDeadLetterPublisher:
    """记录死信生命周期和发布内容。"""

    def __init__(
        self,
        start_error: Exception | None = None,
        publish_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.start_error = start_error
        self.publish_error = publish_error
        self.close_error = close_error
        self.start_count = 0
        self.close_count = 0
        self.records: list[DeadLetterRecord] = []

    async def start(self) -> None:
        self.start_count += 1
        if self.start_error is not None:
            raise self.start_error

    async def publish(self, record: DeadLetterRecord) -> None:
        if self.publish_error is not None:
            raise self.publish_error
        self.records.append(record)

    async def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class FakeKafkaConsumer:
    """记录轮询、提交和游标回退操作的Consumer替身。"""

    def __init__(
        self,
        batches: dict | None = None,
        start_error: Exception | None = None,
        poll_error: Exception | None = None,
        commit_error: Exception | None = None,
        seek_error: Exception | None = None,
        stop_error: Exception | None = None,
        assignments: set[TopicPartition] | None = None,
        positions: dict[TopicPartition, int] | None = None,
        highwaters: dict[TopicPartition, int | None] | None = None,
        position_error: Exception | None = None,
        block_position: bool = False,
    ) -> None:
        self.batches = batches or {}
        self.start_error = start_error
        self.poll_error = poll_error
        self.commit_error = commit_error
        self.seek_error = seek_error
        self.stop_error = stop_error
        self.assignments = assignments or set()
        self.positions = positions or {}
        self.highwaters = highwaters or {}
        self.position_error = position_error
        self.block_position = block_position
        self.start_count = 0
        self.stop_count = 0
        self.poll_calls: list[dict[str, int]] = []
        self.position_calls: list[TopicPartition] = []
        self.commits: list[dict] = []
        self.seeks: list[tuple[TopicPartition, int]] = []

    async def start(self) -> None:
        self.start_count += 1
        if self.start_error is not None:
            raise self.start_error

    async def stop(self) -> None:
        self.stop_count += 1
        if self.stop_error is not None:
            raise self.stop_error

    async def getmany(
        self,
        *,
        timeout_ms: int,
        max_records: int,
    ) -> dict:
        self.poll_calls.append({"timeout_ms": timeout_ms, "max_records": max_records})
        if self.poll_error is not None:
            raise self.poll_error
        return self.batches

    async def commit(self, offsets: dict) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.commits.append(offsets)

    def seek(self, partition: TopicPartition, offset: int) -> None:
        if self.seek_error is not None:
            raise self.seek_error
        self.seeks.append((partition, offset))
        self.positions[partition] = offset

    def assignment(self) -> set[TopicPartition]:
        return set(self.assignments)

    async def position(self, partition: TopicPartition) -> int:
        self.position_calls.append(partition)
        if self.position_error is not None:
            raise self.position_error
        if self.block_position:
            await asyncio.Event().wait()
        return self.positions[partition]

    def highwater(self, partition: TopicPartition) -> int | None:
        return self.highwaters.get(partition)


class RecordingConsumerFactory:
    """记录Consumer构造参数。"""

    def __init__(self, consumer: FakeKafkaConsumer) -> None:
        self.consumer = consumer
        self.topics: tuple[str, ...] = ()
        self.options: dict[str, object] | None = None

    def __call__(self, *topics: str, **options):
        self.topics = topics
        self.options = options
        return self.consumer


def build_consumer_config(**overrides) -> KafkaConsumerConfig:
    """构造测试使用的Consumer配置。"""
    values = {
        "bootstrap_servers": ("kafka-1:9092",),
        "topic": PARTITION.topic,
        "group_id": "devops-agent-rca-test",
        "client_id": "rca-consumer-test",
        "poll_timeout_ms": 250,
    }
    values.update(overrides)
    return KafkaConsumerConfig(**values)


def build_consumer(
    fake_consumer: FakeKafkaConsumer,
    result: MessageProcessingResult,
    dead_letter: FakeDeadLetterPublisher | None = None,
    *,
    config: KafkaConsumerConfig | None = None,
    monotonic_clock=None,
) -> tuple[RCAKafkaConsumer, FakeDeadLetterPublisher, RecordingConsumerFactory]:
    """组装可观测的单条消费测试对象。"""
    publisher = dead_letter or FakeDeadLetterPublisher()
    factory = RecordingConsumerFactory(fake_consumer)
    consumer = RCAKafkaConsumer(
        config=config or build_consumer_config(),
        processor=FakeRecordProcessor(result),
        dead_letter_publisher=publisher,
        consumer_factory=factory,
        **({"monotonic_clock": monotonic_clock} if monotonic_clock is not None else {}),
    )
    return consumer, publisher, factory


async def test_lifecycle_and_empty_poll_use_manual_commit_configuration() -> None:
    """生命周期应幂等，空轮询不产生offset副作用。"""
    fake = FakeKafkaConsumer()
    consumer, dead_letter, factory = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
    )

    await consumer.start()
    await consumer.start()
    result = await consumer.run_once()
    await consumer.close()
    await consumer.close()

    assert result.polled == 0
    assert fake.start_count == 1
    assert fake.stop_count == 1
    assert dead_letter.start_count == 1
    assert dead_letter.close_count == 1
    assert factory.topics == (PARTITION.topic,)
    assert factory.options is not None
    assert factory.options["enable_auto_commit"] is False
    assert factory.options["group_id"] == "devops-agent-rca-test"
    assert fake.poll_calls == [{"timeout_ms": 250, "max_records": 1}]


async def test_empty_poll_collects_complete_partition_lag() -> None:
    """空轮询也应汇总全部已分配分区的当前位置Lag。"""
    other = TopicPartition(PARTITION.topic, 3)
    fake = FakeKafkaConsumer(
        assignments={PARTITION, other},
        positions={PARTITION: 8, other: 20},
        highwaters={PARTITION: 10, other: 25},
    )
    consumer, _, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
    )
    await consumer.start()

    result = await consumer.run_once()

    assert result.lag_snapshot == ConsumerLagSnapshot(
        source_up=True,
        assigned_partitions=2,
        measured_partitions=2,
        total_lag=7,
        max_partition_lag=5,
    )


async def test_missing_highwater_returns_partial_unavailable_lag() -> None:
    """尚未取得FetchResponse的分区不能被伪装成零Lag。"""
    other = TopicPartition(PARTITION.topic, 3)
    fake = FakeKafkaConsumer(
        assignments={PARTITION, other},
        positions={PARTITION: 8, other: 20},
        highwaters={PARTITION: 10, other: None},
    )
    consumer, _, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
    )
    await consumer.start()

    result = await consumer.run_once()

    assert result.lag_snapshot == ConsumerLagSnapshot(
        source_up=False,
        assigned_partitions=2,
        measured_partitions=1,
        total_lag=2,
        max_partition_lag=2,
    )


@pytest.mark.parametrize(
    "fake",
    [
        FakeKafkaConsumer(
            assignments={PARTITION},
            positions={PARTITION: 8},
            highwaters={PARTITION: 10},
            position_error=RuntimeError("position unavailable"),
        ),
        FakeKafkaConsumer(
            assignments={PARTITION},
            positions={PARTITION: 8},
            highwaters={PARTITION: 10},
            block_position=True,
        ),
    ],
)
async def test_lag_failure_does_not_change_message_disposition(
    fake: FakeKafkaConsumer,
) -> None:
    """位置读取失败或超时只能降低指标可信度，不能让消息重试。"""
    consumer, _, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
        config=build_consumer_config(lag_query_timeout_ms=10),
    )
    await consumer.start()

    result = await consumer.run_once()

    assert result.polled == 0
    assert result.retried == 0
    assert result.lag_snapshot is not None
    assert result.lag_snapshot.source_up is False


async def test_lag_partition_limit_prevents_unbounded_position_reads() -> None:
    """异常大分区分配应直接标记不可用，不进行逐分区查询。"""
    other = TopicPartition(PARTITION.topic, 3)
    fake = FakeKafkaConsumer(
        assignments={PARTITION, other},
        positions={PARTITION: 8, other: 20},
        highwaters={PARTITION: 10, other: 25},
    )
    consumer, _, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
        config=build_consumer_config(max_lag_partitions=1),
    )
    await consumer.start()

    result = await consumer.run_once()

    assert result.lag_snapshot == ConsumerLagSnapshot(
        source_up=False,
        assigned_partitions=2,
        measured_partitions=0,
        total_lag=0,
        max_partition_lag=0,
    )


async def test_lag_snapshot_is_reused_within_sampling_interval() -> None:
    """高吞吐轮询应复用短缓存，避免逐消息遍历全部分区。"""
    now = [100.0]
    fake = FakeKafkaConsumer(
        assignments={PARTITION},
        positions={PARTITION: 8},
        highwaters={PARTITION: 10},
    )
    consumer, _, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
        config=build_consumer_config(
            lag_sample_interval_seconds=5,
        ),
        monotonic_clock=lambda: now[0],
    )
    await consumer.start()

    first = await consumer.run_once()
    second = await consumer.run_once()
    now[0] = 105.0
    third = await consumer.run_once()

    assert first.lag_snapshot == second.lag_snapshot
    assert third.lag_snapshot == first.lag_snapshot
    assert fake.position_calls == [PARTITION, PARTITION]


async def test_invalid_sampling_clock_does_not_change_ack_result() -> None:
    """监控时钟异常不能在消息已提交后把成功改写成重试。"""
    fake = FakeKafkaConsumer()
    consumer, _, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
        monotonic_clock=lambda: float("nan"),
    )
    await consumer.start()

    result = await consumer.run_once()

    assert result.retried == 0
    assert result.lag_snapshot == ConsumerLagSnapshot(
        source_up=False,
        assigned_partitions=0,
        measured_partitions=0,
        total_lag=0,
        max_partition_lag=0,
    )


async def test_ack_commits_next_offset_without_dead_letter() -> None:
    """业务处理成功后提交下一offset。"""
    record = FakeConsumerRecord()
    fake = FakeKafkaConsumer({PARTITION: [record]})
    consumer, dead_letter, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
    )
    await consumer.start()

    result = await consumer.run_once()

    assert result.acknowledged == 1
    assert result.dead_lettered == 0
    assert fake.seeks == []
    assert dead_letter.records == []
    assert fake.commits == [{PARTITION: OffsetAndMetadata(record.offset + 1, "")}]


async def test_retry_rewinds_without_committing() -> None:
    """可重试异常必须回到原offset，不能提交或写死信。"""
    record = FakeConsumerRecord()
    fake = FakeKafkaConsumer(
        {PARTITION: [record]},
        assignments={PARTITION},
        positions={PARTITION: record.offset + 1},
        highwaters={PARTITION: 10},
    )
    consumer, dead_letter, _ = build_consumer(
        fake,
        MessageProcessingResult(
            MessageDisposition.RETRY,
            reason_code="PERSISTENCE_ERROR",
            reason="database unavailable",
        ),
    )
    await consumer.start()

    result = await consumer.run_once()

    assert result.retried == 1
    assert fake.seeks == [(PARTITION, record.offset)]
    assert fake.commits == []
    assert dead_letter.records == []
    assert result.lag_snapshot is not None
    assert result.lag_snapshot.total_lag == 3


async def test_dead_letter_is_published_before_source_offset_commit() -> None:
    """不可重试消息写入死信成功后才允许确认源消息。"""
    record = FakeConsumerRecord()
    fake = FakeKafkaConsumer({PARTITION: [record]})
    consumer, dead_letter, _ = build_consumer(
        fake,
        MessageProcessingResult(
            MessageDisposition.DEAD_LETTER,
            reason_code="INVALID_JSON",
            reason="JSON is invalid",
        ),
    )
    await consumer.start()

    result = await consumer.run_once()

    assert result.dead_lettered == 1
    assert result.acknowledged == 1
    assert len(dead_letter.records) == 1
    published = dead_letter.records[0]
    assert published.value == record.value
    assert published.key == record.key
    assert published.headers == tuple(record.headers or ())
    assert published.reason_code == "INVALID_JSON"
    assert fake.commits == [{PARTITION: OffsetAndMetadata(record.offset + 1, "")}]


@pytest.mark.parametrize("failure_stage", ["dead_letter", "commit"])
async def test_publish_or_commit_failure_rewinds_and_raises(
    failure_stage: str,
) -> None:
    """死信或提交失败都必须回退源游标，避免当前进程跳过消息。"""
    record = FakeConsumerRecord()
    fake = FakeKafkaConsumer(
        {PARTITION: [record]},
        commit_error=(
            RuntimeError("commit failed") if failure_stage == "commit" else None
        ),
    )
    dead_letter = FakeDeadLetterPublisher(
        publish_error=(
            RuntimeError("dead letter failed")
            if failure_stage == "dead_letter"
            else None
        )
    )
    consumer, _, _ = build_consumer(
        fake,
        MessageProcessingResult(
            MessageDisposition.DEAD_LETTER,
            reason_code="INVALID_JSON",
            reason="invalid",
        ),
        dead_letter,
    )
    await consumer.start()

    with pytest.raises(MessageConsumeError):
        await consumer.run_once()

    assert fake.seeks == [(PARTITION, record.offset)]
    assert fake.commits == []


async def test_poll_failure_is_mapped_to_stable_consume_error() -> None:
    """Broker轮询故障应转换为稳定应用异常。"""
    fake = FakeKafkaConsumer(poll_error=RuntimeError("broker unavailable"))
    consumer, _, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
    )
    await consumer.start()

    with pytest.raises(MessageConsumeError, match="poll"):
        await consumer.run_once()


async def test_consumer_start_failure_cleans_started_dead_letter_channel() -> None:
    """Consumer启动失败时必须回收已启动的死信Producer。"""
    fake = FakeKafkaConsumer(start_error=RuntimeError("group unavailable"))
    consumer, dead_letter, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
    )

    with pytest.raises(MessageConsumeError, match="start"):
        await consumer.start()

    assert fake.stop_count == 1
    assert dead_letter.start_count == 1
    assert dead_letter.close_count == 1


async def test_consumer_start_cleanup_failures_are_logged_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """反向清理失败必须可见，但不能记录第三方异常正文。"""
    fake = FakeKafkaConsumer(
        start_error=RuntimeError("group token=primary-secret"),
        stop_error=RuntimeError("password=consumer-secret"),
    )
    dead_letter = FakeDeadLetterPublisher(
        close_error=RuntimeError("password=dead-letter-secret"),
    )
    consumer, _, _ = build_consumer(
        fake,
        MessageProcessingResult(MessageDisposition.ACK),
        dead_letter,
    )

    with caplog.at_level(
        logging.WARNING,
        logger=(
            "devops_agent_platform.infrastructure.adapters.kafka.consumer"
        ),
    ):
        with pytest.raises(MessageConsumeError, match="start"):
            await consumer.start()

    messages = [record.getMessage() for record in caplog.records]
    assert (
        "RCA Kafka Consumer启动失败后的Consumer清理失败"
        in messages
    )
    assert (
        "RCA Kafka Consumer启动失败后的死信通道清理失败"
        in messages
    )
    assert fake.stop_count == 1
    assert dead_letter.close_count == 1
    assert "primary-secret" not in caplog.text
    assert "consumer-secret" not in caplog.text
    assert "dead-letter-secret" not in caplog.text


class FakeProducer:
    """记录KafkaDeadLetterPublisher的发送参数。"""

    def __init__(
        self,
        send_error: Exception | None = None,
        start_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.send_error = send_error
        self.start_error = start_error
        self.stop_error = stop_error
        self.start_count = 0
        self.stop_count = 0
        self.messages: list[dict[str, object]] = []

    async def start(self) -> None:
        self.start_count += 1
        if self.start_error is not None:
            raise self.start_error

    async def stop(self) -> None:
        self.stop_count += 1
        if self.stop_error is not None:
            raise self.stop_error

    async def send_and_wait(
        self,
        topic: str,
        value: bytes,
        key: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> object:
        if self.send_error is not None:
            raise self.send_error
        self.messages.append(
            {
                "topic": topic,
                "value": value,
                "key": key,
                "headers": headers,
            }
        )
        return object()


class ProducerFactory:
    """为死信Publisher返回固定Producer并记录选项。"""

    def __init__(self, producer: FakeProducer) -> None:
        self.producer = producer
        self.options: dict[str, object] | None = None

    def __call__(self, **options):
        self.options = options
        return self.producer


def build_dead_letter_record() -> DeadLetterRecord:
    """构造包含非UTF-8字节的死信记录。"""
    return DeadLetterRecord(
        source_topic=PARTITION.topic,
        source_partition=PARTITION.partition,
        source_offset=7,
        key=b"\x00key",
        value=b"\xffinvalid-json",
        headers=(("trace-id", b"trc_001"),),
        reason_code="INVALID_JSON",
        reason="message cannot be decoded",
        failed_at=NOW,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_topic": "devops-agent.events.v1\nforged"},
        {"headers": (("trace\nid", b"trc_001"),)},
        {"reason_code": "INVALID\nJSON"},
        {"reason": "message cannot\x7fbe decoded"},
        {"reason": "message cannot\nbe decoded"},
    ],
)
def test_dead_letter_record_rejects_control_character_metadata(
    overrides: dict[str, object],
) -> None:
    """死信只允许原始字节脏，索引和原因文本必须保持单行。"""
    record = build_dead_letter_record()
    values = {
        "source_topic": record.source_topic,
        "source_partition": record.source_partition,
        "source_offset": record.source_offset,
        "key": record.key,
        "value": record.value,
        "headers": record.headers,
        "reason_code": record.reason_code,
        "reason": record.reason,
        "failed_at": record.failed_at,
    }
    values.update(overrides)

    with pytest.raises(AppValidationError, match="control characters"):
        DeadLetterRecord(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"bootstrap_servers": ("kafka\n1:9092",)},
        {"group_id": "rca\nconsumer"},
        {"client_id": "client\x7fforged"},
        {"client_id": "client\rforged"},
    ],
)
def test_consumer_config_rejects_control_character_identity(
    overrides: dict[str, object],
) -> None:
    """Consumer身份会进入Broker日志和运维界面，不能携带换行污染。"""
    with pytest.raises(AppValidationError, match="control characters"):
        build_consumer_config(**overrides)


async def test_dead_letter_publisher_preserves_binary_record_as_base64() -> None:
    """死信序列化必须无损保留任意二进制key、value和headers。"""
    producer = FakeProducer()
    factory = ProducerFactory(producer)
    publisher = KafkaDeadLetterPublisher(
        KafkaPublisherConfig(
            bootstrap_servers=("kafka-1:9092",),
            topic="devops-agent.events.v1.dlq",
            client_id="rca-dlq-test",
        ),
        factory,
    )
    await publisher.start()

    await publisher.publish(build_dead_letter_record())
    await publisher.close()

    message = producer.messages[0]
    payload = json.loads(message["value"])
    assert message["topic"] == "devops-agent.events.v1.dlq"
    assert message["key"] == b"devops-agent.events.v1:2:7"
    assert payload["dead_letter_id"] == "devops-agent.events.v1:2:7"
    assert base64.b64decode(payload["key_base64"]) == b"\x00key"
    assert base64.b64decode(payload["value_base64"]) == b"\xffinvalid-json"
    assert payload["reason_code"] == "INVALID_JSON"
    assert producer.start_count == 1
    assert producer.stop_count == 1
    assert factory.options is not None
    assert factory.options["enable_idempotence"] is True


async def test_dead_letter_publish_failure_is_mapped() -> None:
    """死信Broker发送失败应向Consumer报告，禁止提交源offset。"""
    publisher = KafkaDeadLetterPublisher(
        KafkaPublisherConfig(
            bootstrap_servers=("kafka-1:9092",),
            topic="devops-agent.events.v1.dlq",
        ),
        ProducerFactory(FakeProducer(send_error=RuntimeError("send failed"))),
    )
    await publisher.start()

    with pytest.raises(EventPublishError, match="dead-letter"):
        await publisher.publish(build_dead_letter_record())


async def test_dead_letter_start_cleanup_failure_is_logged_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """死信Producer启动回滚失败应留固定告警且不泄露异常正文。"""
    producer = FakeProducer(
        start_error=RuntimeError("token=primary-secret"),
        stop_error=RuntimeError("password=cleanup-secret"),
    )
    publisher = KafkaDeadLetterPublisher(
        KafkaPublisherConfig(
            bootstrap_servers=("kafka-1:9092",),
            topic="devops-agent.events.v1.dlq",
        ),
        ProducerFactory(producer),
    )

    with caplog.at_level(
        logging.WARNING,
        logger=(
            "devops_agent_platform.infrastructure.adapters.kafka.dead_letter"
        ),
    ):
        with pytest.raises(EventPublishError, match="start"):
            await publisher.start()

    assert producer.stop_count == 1
    assert (
        "Kafka死信Producer启动失败后的资源清理失败"
        in [record.getMessage() for record in caplog.records]
    )
    assert "primary-secret" not in caplog.text
    assert "cleanup-secret" not in caplog.text
