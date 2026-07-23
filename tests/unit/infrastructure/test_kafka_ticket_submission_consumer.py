import logging
from dataclasses import dataclass

import pytest
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from devops_agent_platform.application.exceptions import MessageConsumeError
from devops_agent_platform.application.services import (
    ticket_submission_record_processor as ticket_processor,
)
from devops_agent_platform.infrastructure.adapters.kafka import (
    KafkaConsumerConfig,
    TicketSubmissionKafkaConsumer,
)
from devops_agent_platform.ports.messaging import DeadLetterRecord

PARTITION = TopicPartition("devops-agent.events.v1", 4)
SECOND_PARTITION = TopicPartition("devops-agent.events.v1", 5)
MessageDisposition = ticket_processor.TicketSubmissionMessageDisposition
ProcessingResult = ticket_processor.TicketSubmissionProcessingResult


@dataclass
class FakeConsumerRecord:
    """测试使用的最小Kafka ConsumerRecord。"""

    topic: str = PARTITION.topic
    partition: int = PARTITION.partition
    offset: int = 11
    key: bytes | None = b"submission_001"
    value: bytes = b'{"event_type":"ticket_submission.requested"}'
    headers: list[tuple[str, bytes]] | None = None

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = [("trace-id", b"trc_ticket_001")]


class FakeRecordProcessor:
    """返回预设工单提交消息处置或异常。"""

    def __init__(
        self,
        result: ProcessingResult,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.values: list[bytes] = []

    async def process(self, value: bytes) -> ProcessingResult:
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
        self.poll_calls.append(
            {"timeout_ms": timeout_ms, "max_records": max_records}
        )
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


def processing_result(
    disposition: MessageDisposition,
    *,
    reason_code: str | None = None,
    reason: str | None = None,
) -> ProcessingResult:
    """构造应用层消息处理结果。"""
    return ProcessingResult(
        disposition=disposition,
        reason_code=reason_code,
        reason=reason,
    )


def build_consumer_config(**overrides) -> KafkaConsumerConfig:
    """构造测试使用的Consumer配置。"""
    values = {
        "bootstrap_servers": ("kafka-1:9092",),
        "topic": PARTITION.topic,
        "group_id": "devops-agent-ticket-submission-test",
        "client_id": "ticket-submission-consumer-test",
        "poll_timeout_ms": 250,
    }
    values.update(overrides)
    return KafkaConsumerConfig(**values)


def build_consumer(
    fake_consumer: FakeKafkaConsumer,
    result: ProcessingResult,
    dead_letter: FakeDeadLetterPublisher | None = None,
    *,
    processor_error: Exception | None = None,
    config: KafkaConsumerConfig | None = None,
    monotonic_clock=None,
) -> tuple[
    TicketSubmissionKafkaConsumer,
    FakeDeadLetterPublisher,
    RecordingConsumerFactory,
]:
    """组装可观测的单条消费测试对象。"""
    publisher = dead_letter or FakeDeadLetterPublisher()
    factory = RecordingConsumerFactory(fake_consumer)
    consumer = TicketSubmissionKafkaConsumer(
        config=config or build_consumer_config(),
        processor=FakeRecordProcessor(result, error=processor_error),
        dead_letter_publisher=publisher,
        consumer_factory=factory,
        **(
            {"monotonic_clock": monotonic_clock}
            if monotonic_clock is not None
            else {}
        ),
    )
    return consumer, publisher, factory


async def test_lifecycle_and_empty_poll_use_manual_commit_configuration() -> None:
    """生命周期应幂等，空轮询不产生offset副作用。"""
    fake = FakeKafkaConsumer()
    consumer, dead_letter, factory = build_consumer(
        fake,
        processing_result(MessageDisposition.ACK),
    )

    await consumer.start()
    await consumer.start()
    result = await consumer.run_once()
    await consumer.close()
    await consumer.close()

    assert result is None
    assert fake.start_count == 1
    assert fake.stop_count == 1
    assert dead_letter.start_count == 1
    assert dead_letter.close_count == 1
    assert factory.topics == (PARTITION.topic,)
    assert factory.options is not None
    assert factory.options["enable_auto_commit"] is False
    assert factory.options["group_id"] == "devops-agent-ticket-submission-test"
    assert fake.poll_calls == [{"timeout_ms": 250, "max_records": 1}]


async def test_empty_poll_collects_and_caches_lag_snapshot() -> None:
    """空轮询也应刷新Lag，但采样间隔内不能重复查询Kafka位置。"""
    ticks = iter([10.0, 12.0])
    fake = FakeKafkaConsumer(
        assignments={PARTITION, SECOND_PARTITION},
        positions={
            PARTITION: 8,
            SECOND_PARTITION: 3,
        },
        highwaters={
            PARTITION: 18,
            SECOND_PARTITION: 30,
        },
    )
    consumer, _, _ = build_consumer(
        fake,
        processing_result(MessageDisposition.ACK),
        config=build_consumer_config(lag_sample_interval_seconds=5.0),
        monotonic_clock=lambda: next(ticks),
    )
    await consumer.start()

    first = await consumer.run_once()
    second = await consumer.run_once()

    assert first is None
    assert second is None
    assert consumer.lag_snapshot is not None
    assert consumer.lag_snapshot.source_up is True
    assert consumer.lag_snapshot.assigned_partitions == 2
    assert consumer.lag_snapshot.measured_partitions == 2
    assert consumer.lag_snapshot.total_lag == 37
    assert consumer.lag_snapshot.max_partition_lag == 27
    assert fake.position_calls == [PARTITION, SECOND_PARTITION]


async def test_ack_commits_next_offset_without_dead_letter() -> None:
    """业务处理成功后提交下一offset，并把ACK结果交给Runner计数。"""
    record = FakeConsumerRecord()
    fake = FakeKafkaConsumer({PARTITION: [record]})
    expected = processing_result(MessageDisposition.ACK)
    consumer, dead_letter, _ = build_consumer(fake, expected)
    await consumer.start()

    result = await consumer.run_once()

    assert result is expected
    assert dead_letter.records == []
    assert fake.seeks == []
    assert fake.commits == [
        {PARTITION: OffsetAndMetadata(record.offset + 1, "")}
    ]


async def test_retry_rewinds_without_committing() -> None:
    """可重试异常必须回到原offset，不能提交或写死信。"""
    record = FakeConsumerRecord()
    fake = FakeKafkaConsumer({PARTITION: [record]})
    expected = processing_result(
        MessageDisposition.RETRY,
        reason_code="PERSISTENCE_ERROR",
        reason="database unavailable",
    )
    consumer, dead_letter, _ = build_consumer(fake, expected)
    await consumer.start()

    result = await consumer.run_once()

    assert result is expected
    assert fake.seeks == [(PARTITION, record.offset)]
    assert fake.commits == []
    assert dead_letter.records == []


async def test_dead_letter_is_published_before_source_offset_commit() -> None:
    """不可重试消息写入死信成功后才允许确认源消息。"""
    record = FakeConsumerRecord()
    fake = FakeKafkaConsumer({PARTITION: [record]})
    expected = processing_result(
        MessageDisposition.DEAD_LETTER,
        reason_code="INVALID_JSON",
        reason="JSON is invalid",
    )
    consumer, dead_letter, _ = build_consumer(fake, expected)
    await consumer.start()

    result = await consumer.run_once()

    assert result is expected
    assert len(dead_letter.records) == 1
    published = dead_letter.records[0]
    assert published.value == record.value
    assert published.key == record.key
    assert published.headers == tuple(record.headers or ())
    assert published.reason_code == "INVALID_JSON"
    assert fake.commits == [
        {PARTITION: OffsetAndMetadata(record.offset + 1, "")}
    ]


@pytest.mark.parametrize("failure_stage", ["dead_letter", "commit"])
async def test_publish_or_commit_failure_rewinds_and_raises(
    failure_stage: str,
) -> None:
    """死信或提交失败都必须回退源游标，避免当前进程跳过消息。"""
    record = FakeConsumerRecord()
    fake = FakeKafkaConsumer(
        {PARTITION: [record]},
        commit_error=(
            RuntimeError("commit failed")
            if failure_stage == "commit"
            else None
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
        processing_result(
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


async def test_processor_failure_rewinds_and_raises_stable_error() -> None:
    """处理器异常必须回退offset，并转换为稳定消费异常。"""
    record = FakeConsumerRecord()
    fake = FakeKafkaConsumer({PARTITION: [record]})
    consumer, _, _ = build_consumer(
        fake,
        processing_result(MessageDisposition.ACK),
        processor_error=RuntimeError("handler failed"),
    )
    await consumer.start()

    with pytest.raises(MessageConsumeError, match="process"):
        await consumer.run_once()

    assert fake.seeks == [(PARTITION, record.offset)]
    assert fake.commits == []


async def test_poll_failure_is_mapped_to_stable_consume_error() -> None:
    """Broker轮询故障应转换为稳定应用异常。"""
    fake = FakeKafkaConsumer(poll_error=RuntimeError("broker unavailable"))
    consumer, _, _ = build_consumer(
        fake,
        processing_result(MessageDisposition.ACK),
    )
    await consumer.start()

    with pytest.raises(MessageConsumeError, match="poll"):
        await consumer.run_once()


async def test_consumer_start_failure_cleans_started_dead_letter_channel() -> None:
    """Consumer启动失败时必须回收已启动的死信Producer。"""
    fake = FakeKafkaConsumer(start_error=RuntimeError("group unavailable"))
    consumer, dead_letter, _ = build_consumer(
        fake,
        processing_result(MessageDisposition.ACK),
    )

    with pytest.raises(MessageConsumeError, match="start"):
        await consumer.start()

    assert fake.stop_count == 1
    assert dead_letter.start_count == 1
    assert dead_letter.close_count == 1


async def test_consumer_start_cleanup_failures_are_logged_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """工单Consumer反向清理失败应可观测且不泄露异常正文。"""
    fake = FakeKafkaConsumer(
        start_error=RuntimeError("group token=primary-secret"),
        stop_error=RuntimeError("password=consumer-secret"),
    )
    dead_letter = FakeDeadLetterPublisher(
        close_error=RuntimeError("password=dead-letter-secret"),
    )
    consumer, _, _ = build_consumer(
        fake,
        processing_result(MessageDisposition.ACK),
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
        "工单提交Kafka Consumer启动失败后的Consumer清理失败"
        in messages
    )
    assert (
        "工单提交Kafka Consumer启动失败后的死信通道清理失败"
        in messages
    )
    assert fake.stop_count == 1
    assert dead_letter.close_count == 1
    assert "primary-secret" not in caplog.text
    assert "consumer-secret" not in caplog.text
    assert "dead-letter-secret" not in caplog.text


async def test_run_once_before_start_is_rejected() -> None:
    """未启动Consumer时禁止轮询，避免隐藏生命周期错误。"""
    consumer, _, _ = build_consumer(
        FakeKafkaConsumer(),
        processing_result(MessageDisposition.ACK),
    )

    with pytest.raises(MessageConsumeError, match="not started"):
        await consumer.run_once()
