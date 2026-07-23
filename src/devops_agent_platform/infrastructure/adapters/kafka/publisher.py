import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.exceptions import EventPublishError
from devops_agent_platform.domain.exceptions import AppValidationError

logger = logging.getLogger(__name__)
_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,248}$")
_SECURITY_PROTOCOLS = frozenset({"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"})
_SASL_MECHANISMS = frozenset({"PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"})


class KafkaProducerProtocol(Protocol):
    """KafkaEventPublisher实际使用的Producer最小接口。"""

    async def start(self) -> None:
        """建立Kafka连接并完成元数据初始化。"""
        ...

    async def stop(self) -> None:
        """刷新缓冲区并关闭网络资源。"""
        ...

    async def send_and_wait(
        self,
        topic: str,
        value: bytes,
        key: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> Any:
        """发送一条消息并等待Broker确认。"""
        ...


ProducerFactory = Callable[..., KafkaProducerProtocol]


@dataclass(frozen=True)
class KafkaPublisherConfig:
    """Kafka Producer连接、安全和吞吐配置。"""

    bootstrap_servers: tuple[str, ...]
    topic: str = "devops-agent.events.v1"
    client_id: str = "devops-agent-platform"
    security_protocol: str = "PLAINTEXT"
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = field(default=None, repr=False)
    request_timeout_ms: int = 10_000
    linger_ms: int = 5
    max_request_size: int = 1_048_576

    def __post_init__(self) -> None:
        """校验Broker、Topic、安全协议和数值边界。"""
        if not isinstance(self.bootstrap_servers, tuple):
            raise AppValidationError("bootstrap_servers must be a tuple")
        if not self.bootstrap_servers:
            raise AppValidationError("bootstrap_servers must not be empty")
        for server in self.bootstrap_servers:
            self._validate_text("bootstrap_server", server, 255)
        if (
            not isinstance(self.topic, str)
            or _TOPIC_PATTERN.fullmatch(self.topic) is None
        ):
            raise AppValidationError("topic contains unsupported characters")
        self._validate_text("client_id", self.client_id, 128)
        if self.security_protocol not in _SECURITY_PROTOCOLS:
            raise AppValidationError("unsupported Kafka security_protocol")
        if self.security_protocol.startswith("SASL"):
            self._validate_text("sasl_mechanism", self.sasl_mechanism, 64)
            self._validate_text("sasl_username", self.sasl_username, 256)
            self._validate_text("sasl_password", self.sasl_password, 1024)
            if self.sasl_mechanism not in _SASL_MECHANISMS:
                raise AppValidationError("unsupported Kafka sasl_mechanism")
        self._validate_int(
            "request_timeout_ms",
            self.request_timeout_ms,
            minimum=100,
            maximum=120_000,
        )
        self._validate_int(
            "linger_ms",
            self.linger_ms,
            minimum=0,
            maximum=1000,
        )
        self._validate_int(
            "max_request_size",
            self.max_request_size,
            minimum=1024,
            maximum=10 * 1024 * 1024,
        )

    @staticmethod
    def _validate_text(
        field_name: str,
        value: object,
        max_length: int,
    ) -> None:
        if not isinstance(value, str):
            raise AppValidationError(f"{field_name} must be a string")
        if not 1 <= len(value) <= max_length:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {max_length}"
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


class KafkaEventPublisher:
    """使用单个长生命周期AIOKafkaProducer发布Outbox事件。"""

    def __init__(
        self,
        config: KafkaPublisherConfig,
        producer_factory: ProducerFactory | None = None,
    ) -> None:
        self._config = config
        self._producer_factory = producer_factory or AIOKafkaProducer
        self._producer: KafkaProducerProtocol | None = None
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        """幂等启动Producer；失败时不保留半初始化实例。"""
        async with self._lifecycle_lock:
            if self._producer is not None:
                return
            producer: KafkaProducerProtocol | None = None
            try:
                producer = self._producer_factory(**self._producer_options())
                await producer.start()
            except asyncio.CancelledError:
                if producer is not None:
                    await self._best_effort_stop(producer)
                raise
            except Exception as exc:
                if producer is not None:
                    await self._best_effort_stop(producer)
                raise EventPublishError("Could not start Kafka producer") from exc
            self._producer = producer

    async def publish(self, event: OutboxEvent) -> None:
        """按聚合ID分区并等待Kafka Broker确认消息。"""
        producer = self._producer
        if producer is None:
            raise EventPublishError("Kafka producer is not started")

        try:
            await producer.send_and_wait(
                self._config.topic,
                value=self._serialize_event(event),
                key=event.aggregate_id.encode(),
                headers=self._build_headers(event),
            )
        except asyncio.CancelledError:
            raise
        except (KafkaError, OSError, RuntimeError) as exc:
            raise EventPublishError(
                f"Could not publish Kafka event: {event.event_id}"
            ) from exc

    async def close(self) -> None:
        """幂等停止Producer并清理引用。"""
        async with self._lifecycle_lock:
            producer = self._producer
            if producer is None:
                return
            try:
                await producer.stop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise EventPublishError("Could not stop Kafka producer") from exc
            self._producer = None

    def _producer_options(self) -> dict[str, Any]:
        """构造经过校验的aiokafka Producer参数。"""
        options: dict[str, Any] = {
            "bootstrap_servers": list(self._config.bootstrap_servers),
            "client_id": self._config.client_id,
            "acks": "all",
            "enable_idempotence": True,
            "security_protocol": self._config.security_protocol,
            "request_timeout_ms": self._config.request_timeout_ms,
            "linger_ms": self._config.linger_ms,
            "max_request_size": self._config.max_request_size,
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

    @staticmethod
    def _serialize_event(event: OutboxEvent) -> bytes:
        """序列化稳定、版本化的Kafka消息Envelope。"""
        envelope = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "schema_version": event.schema_version,
            "tenant_id": event.tenant_id,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "occurred_at": event.occurred_at.isoformat(),
            "trace_id": event.trace_id,
            "payload": event.payload,
        }
        return json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    @staticmethod
    def _build_headers(event: OutboxEvent) -> list[tuple[str, bytes]]:
        """构造消费者路由、幂等和追踪需要的Kafka Headers。"""
        return [
            ("content-type", b"application/json"),
            ("event-id", event.event_id.encode()),
            ("event-type", event.event_type.encode()),
            ("schema-version", str(event.schema_version).encode()),
            ("tenant-id", event.tenant_id.encode()),
            ("trace-id", event.trace_id.encode()),
        ]

    @staticmethod
    async def _best_effort_stop(producer: KafkaProducerProtocol) -> None:
        """启动失败后尽力回收Producer，同时保留原始启动异常。"""
        try:
            await producer.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Kafka Producer启动失败后的资源清理也失败",
            )
