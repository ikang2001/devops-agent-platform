import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.services.rca_record_processor import (
    MessageDisposition,
    MessageProcessingResult,
)
from devops_agent_platform.infrastructure.adapters.kafka.consumer import (
    KafkaConsumerConfig,
    RCAKafkaConsumer,
)
from devops_agent_platform.infrastructure.adapters.kafka.dead_letter import (
    KafkaDeadLetterPublisher,
)
from devops_agent_platform.infrastructure.adapters.kafka.publisher import (
    KafkaEventPublisher,
    KafkaPublisherConfig,
)

pytestmark = pytest.mark.live


def _bootstrap_servers() -> tuple[str, ...]:
    raw = os.getenv("DEVOPS_AGENT_TEST_KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not raw:
        pytest.fail(
            "DEVOPS_AGENT_TEST_KAFKA_BOOTSTRAP_SERVERS is required in live mode",
            pytrace=False,
        )
    return tuple(part.strip() for part in raw.split(",") if part.strip())


class _RecordingProcessor:
    def __init__(self) -> None:
        self.deliveries: list[bytes] = []

    async def process(self, value: bytes) -> MessageProcessingResult:
        self.deliveries.append(value)
        if value == b"invalid-contract":
            return MessageProcessingResult(
                disposition=MessageDisposition.DEAD_LETTER,
                reason_code="ACCEPTANCE_INVALID",
                reason="Synthetic invalid contract",
            )
        return MessageProcessingResult(disposition=MessageDisposition.ACK)


async def _poll_result(
    consumer: RCAKafkaConsumer,
    *,
    expected_dead_letters: int = 0,
) -> None:
    for _ in range(20):
        result = await consumer.run_once()
        if result.polled:
            assert result.acknowledged == 1
            assert result.dead_lettered == expected_dead_letters
            return
        await asyncio.sleep(0.1)
    pytest.fail("Kafka acceptance record was not consumed", pytrace=False)


async def test_live_publish_commit_restart_duplicate_and_dead_letter() -> None:
    """验证真实 Broker 的发布确认、offset、重复投递与 DLQ 顺序。"""
    servers = _bootstrap_servers()
    suffix = uuid4().hex[:12]
    topic = f"devops-agent-acceptance-{suffix}"
    dead_letter_topic = f"{topic}-dlq"
    group_id = f"devops-agent-acceptance-{suffix}"
    admin = AIOKafkaAdminClient(
        bootstrap_servers=list(servers),
        client_id=f"acceptance-admin-{suffix}",
    )
    await admin.start()
    await admin.create_topics(
        [
            NewTopic(topic, num_partitions=1, replication_factor=1),
            NewTopic(dead_letter_topic, num_partitions=1, replication_factor=1),
        ]
    )
    publisher = KafkaEventPublisher(
        KafkaPublisherConfig(
            bootstrap_servers=servers,
            topic=topic,
            client_id=f"acceptance-publisher-{suffix}",
            linger_ms=0,
        )
    )
    processor = _RecordingProcessor()

    def build_consumer() -> RCAKafkaConsumer:
        return RCAKafkaConsumer(
            KafkaConsumerConfig(
                bootstrap_servers=servers,
                topic=topic,
                group_id=group_id,
                client_id=f"acceptance-consumer-{suffix}",
                poll_timeout_ms=500,
                lag_query_timeout_ms=500,
                lag_sample_interval_seconds=0.1,
            ),
            processor,
            KafkaDeadLetterPublisher(
                KafkaPublisherConfig(
                    bootstrap_servers=servers,
                    topic=dead_letter_topic,
                    client_id=f"acceptance-dlq-{suffix}",
                    linger_ms=0,
                )
            ),
        )

    try:
        await publisher.start()
        event = OutboxEvent(
            event_id=f"evt_{suffix}",
            tenant_id="step4_acceptance",
            aggregate_type="Acceptance",
            aggregate_id=f"acceptance_{suffix}",
            event_type="acceptance.requested",
            schema_version=1,
            payload={"acceptance_id": suffix},
            occurred_at=datetime.now(UTC),
            trace_id=f"trace_{suffix}",
        )
        await publisher.publish(event)
        await publisher.publish(event)

        first = build_consumer()
        await first.start()
        await _poll_result(first)
        await _poll_result(first)
        await first.close()
        assert len(processor.deliveries) == 2
        assert json.loads(processor.deliveries[0])["event_id"] == event.event_id

        # 同消费组重建后不应再次读取已经手动提交的两条 offset。
        restarted = build_consumer()
        await restarted.start()
        empty = await restarted.run_once()
        assert empty.polled == 0

        raw_producer = AIOKafkaProducer(
            bootstrap_servers=list(servers),
            acks="all",
        )
        await raw_producer.start()
        await raw_producer.send_and_wait(topic, b"invalid-contract")
        await raw_producer.stop()
        await _poll_result(restarted, expected_dead_letters=1)
        await restarted.close()

        dlq_consumer = AIOKafkaConsumer(
            dead_letter_topic,
            bootstrap_servers=list(servers),
            group_id=f"dlq-reader-{suffix}",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        await dlq_consumer.start()
        try:
            record = await asyncio.wait_for(dlq_consumer.getone(), timeout=10)
            envelope = json.loads(record.value)
            assert envelope["reason_code"] == "ACCEPTANCE_INVALID"
            assert envelope["source_topic"] == topic
        finally:
            await dlq_consumer.stop()
    finally:
        await publisher.close()
        try:
            await admin.delete_topics([topic, dead_letter_topic])
        finally:
            await admin.close()
