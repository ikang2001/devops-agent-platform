import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
from aiokafka.errors import KafkaConnectionError

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.exceptions import EventPublishError
from devops_agent_platform.application.messages.rca_requested import (
    RCARequestedEventV1,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.adapters.kafka import (
    KafkaEventPublisher,
    KafkaPublisherConfig,
)


def build_event() -> OutboxEvent:
    """构造Kafka发布测试使用的事件。"""
    return OutboxEvent(
        event_id="evt_001",
        tenant_id="tenant_001",
        aggregate_type="Incident",
        aggregate_id="inc_001",
        event_type="incident.created",
        schema_version=1,
        payload={"message": "服务异常", "incident_id": "inc_001"},
        occurred_at=datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        trace_id="trc_001",
    )


def build_config(**overrides) -> KafkaPublisherConfig:
    """构造有效Kafka配置并允许测试覆盖单个字段。"""
    values = {
        "bootstrap_servers": ("kafka-1:9092", "kafka-2:9092"),
        "topic": "devops-agent.events.v1",
        "client_id": "devops-agent-test",
    }
    values.update(overrides)
    return KafkaPublisherConfig(**values)


class FakeProducer:
    """记录生命周期和发送参数的Producer替身。"""

    def __init__(
        self,
        start_error: Exception | None = None,
        send_error: BaseException | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.start_error = start_error
        self.send_error = send_error
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


class RecordingProducerFactory:
    """记录传给AIOKafkaProducer的配置。"""

    def __init__(self, producer: FakeProducer) -> None:
        self.producer = producer
        self.options: dict[str, object] | None = None

    def __call__(self, **options):
        self.options = options
        return self.producer


async def test_lifecycle_is_idempotent_and_uses_reliable_producer_options() -> None:
    producer = FakeProducer()
    factory = RecordingProducerFactory(producer)
    publisher = KafkaEventPublisher(build_config(), factory)

    await publisher.start()
    await publisher.start()
    await publisher.close()
    await publisher.close()

    assert producer.start_count == 1
    assert producer.stop_count == 1
    assert factory.options is not None
    assert factory.options["bootstrap_servers"] == [
        "kafka-1:9092",
        "kafka-2:9092",
    ]
    assert factory.options["acks"] == "all"
    assert factory.options["enable_idempotence"] is True


async def test_publish_uses_aggregate_key_versioned_envelope_and_headers() -> None:
    producer = FakeProducer()
    publisher = KafkaEventPublisher(
        build_config(),
        RecordingProducerFactory(producer),
    )
    event = build_event()
    await publisher.start()

    await publisher.publish(event)

    message = producer.messages[0]
    envelope = json.loads(message["value"])
    headers = dict(message["headers"])
    assert message["topic"] == "devops-agent.events.v1"
    assert message["key"] == b"inc_001"
    assert envelope["event_id"] == event.event_id
    assert envelope["schema_version"] == 1
    assert envelope["payload"]["message"] == "服务异常"
    assert headers["event-id"] == b"evt_001"
    assert headers["trace-id"] == b"trc_001"
    assert headers["content-type"] == b"application/json"


async def test_published_rca_event_matches_consumer_v1_contract() -> None:
    """生产者序列化结果必须能被RCA消费者契约直接解析。"""
    producer = FakeProducer()
    publisher = KafkaEventPublisher(
        build_config(),
        RecordingProducerFactory(producer),
    )
    now = datetime(2026, 6, 28, 16, 0, tzinfo=UTC)
    event = OutboxEvent(
        event_id="evt_rca_001",
        tenant_id="tenant_001",
        aggregate_type="WorkflowRun",
        aggregate_id="wfr_001",
        event_type="rca.requested",
        schema_version=1,
        payload={
            "workflow_run_id": "wfr_001",
            "incident_id": "inc_001",
            "tenant_id": "tenant_001",
            "operator_id": "operator_001",
            "requested_at": now.isoformat(),
        },
        occurred_at=now,
        trace_id="trc_rca_001",
    )
    await publisher.start()

    await publisher.publish(event)

    envelope = json.loads(producer.messages[0]["value"])
    parsed = RCARequestedEventV1.from_envelope(envelope)
    assert parsed.event_id == "evt_rca_001"
    assert parsed.workflow_run_id == "wfr_001"
    assert parsed.requested_at == now


async def test_publish_before_start_is_rejected() -> None:
    publisher = KafkaEventPublisher(
        build_config(),
        RecordingProducerFactory(FakeProducer()),
    )

    with pytest.raises(EventPublishError, match="not started"):
        await publisher.publish(build_event())


async def test_start_failure_is_mapped_and_can_be_retried() -> None:
    producer = FakeProducer(start_error=KafkaConnectionError("unavailable"))
    publisher = KafkaEventPublisher(
        build_config(),
        RecordingProducerFactory(producer),
    )

    with pytest.raises(EventPublishError) as exc_info:
        await publisher.start()
    with pytest.raises(EventPublishError):
        await publisher.start()

    assert exc_info.value.status_code == 503
    assert producer.start_count == 2
    assert producer.stop_count == 2


async def test_start_cleanup_failure_is_logged_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """普通Producer的回滚告警也不能携带底层异常正文。"""
    producer = FakeProducer(
        start_error=KafkaConnectionError("token=primary-secret"),
        stop_error=RuntimeError("password=cleanup-secret"),
    )
    publisher = KafkaEventPublisher(
        build_config(),
        RecordingProducerFactory(producer),
    )

    with caplog.at_level(
        logging.WARNING,
        logger=(
            "devops_agent_platform.infrastructure.adapters.kafka.publisher"
        ),
    ):
        with pytest.raises(EventPublishError):
            await publisher.start()

    assert producer.stop_count == 1
    assert (
        "Kafka Producer启动失败后的资源清理也失败"
        in [record.getMessage() for record in caplog.records]
    )
    assert "primary-secret" not in caplog.text
    assert "cleanup-secret" not in caplog.text


async def test_send_failure_is_mapped_to_event_publish_error() -> None:
    producer = FakeProducer(send_error=KafkaConnectionError("broker unavailable"))
    publisher = KafkaEventPublisher(
        build_config(),
        RecordingProducerFactory(producer),
    )
    await publisher.start()

    with pytest.raises(EventPublishError) as exc_info:
        await publisher.publish(build_event())

    assert exc_info.value.code == "EVENT_PUBLISH_FAILED"
    assert exc_info.value.__cause__ is not None


async def test_publish_cancellation_is_not_mapped_to_dependency_error() -> None:
    producer = FakeProducer(send_error=asyncio.CancelledError())
    publisher = KafkaEventPublisher(
        build_config(),
        RecordingProducerFactory(producer),
    )
    await publisher.start()

    with pytest.raises(asyncio.CancelledError):
        await publisher.publish(build_event())


async def test_stop_failure_keeps_reference_for_cleanup_retry() -> None:
    producer = FakeProducer(stop_error=RuntimeError("close failed"))
    publisher = KafkaEventPublisher(
        build_config(),
        RecordingProducerFactory(producer),
    )
    await publisher.start()

    with pytest.raises(EventPublishError):
        await publisher.close()
    producer.stop_error = None
    await publisher.close()

    assert producer.stop_count == 2


def test_sasl_config_is_forwarded_without_exposing_password_in_repr() -> None:
    config = build_config(
        security_protocol="SASL_SSL",
        sasl_mechanism="SCRAM-SHA-512",
        sasl_username="publisher",
        sasl_password="secret-value",
    )
    publisher = KafkaEventPublisher(config)
    options = publisher._producer_options()

    assert options["sasl_mechanism"] == "SCRAM-SHA-512"
    assert options["sasl_plain_username"] == "publisher"
    assert options["sasl_plain_password"] == "secret-value"
    assert "secret-value" not in repr(config)


@pytest.mark.parametrize(
    "overrides",
    [
        {"bootstrap_servers": ()},
        {"bootstrap_servers": ["kafka:9092"]},
        {"bootstrap_servers": ("kafka\n1:9092",)},
        {"topic": "invalid topic"},
        {"client_id": ""},
        {"client_id": "client\x7fforged"},
        {"client_id": "client\nforged"},
        {"security_protocol": "UNKNOWN"},
        {
            "security_protocol": "SASL_SSL",
            "sasl_mechanism": "GSSAPI",
            "sasl_username": "user",
            "sasl_password": "secret",
        },
        {
            "security_protocol": "SASL_SSL",
            "sasl_mechanism": "PLAIN",
            "sasl_username": "user\rforged",
            "sasl_password": "secret",
        },
        {
            "security_protocol": "SASL_SSL",
            "sasl_mechanism": "PLAIN",
            "sasl_username": "user",
            "sasl_password": "secret\x7fforged",
        },
        {"request_timeout_ms": 99},
        {"linger_ms": 1001},
        {"max_request_size": 1023},
    ],
)
def test_invalid_kafka_config_is_rejected(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(AppValidationError):
        build_config(**overrides)
