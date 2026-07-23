import asyncio
import base64
import json
import logging

from aiokafka import AIOKafkaProducer

from devops_agent_platform.application.exceptions import EventPublishError
from devops_agent_platform.infrastructure.adapters.kafka.publisher import (
    KafkaProducerProtocol,
    KafkaPublisherConfig,
    ProducerFactory,
)
from devops_agent_platform.ports.messaging import DeadLetterRecord

logger = logging.getLogger(__name__)


class KafkaDeadLetterPublisher:
    """把不可重试消费记录可靠发布到Kafka死信Topic。"""

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
        """幂等启动Producer，失败时不保留半初始化对象。"""
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
                raise EventPublishError(
                    "Could not start dead-letter producer"
                ) from exc
            self._producer = producer

    async def publish(self, record: DeadLetterRecord) -> None:
        """发布包含原始消息和处置原因的死信Envelope。"""
        producer = self._producer
        if producer is None:
            raise EventPublishError("Dead-letter producer is not started")
        dead_letter_id = self._dead_letter_id(record)
        try:
            await producer.send_and_wait(
                self._config.topic,
                value=self._serialize(record),
                key=dead_letter_id.encode(),
                headers=[
                    ("content-type", b"application/json"),
                    ("dead-letter-reason", record.reason_code.encode()),
                    ("dead-letter-id", dead_letter_id.encode()),
                    ("source-topic", record.source_topic.encode()),
                ],
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise EventPublishError(
                "Could not publish dead-letter record"
            ) from exc

    async def close(self) -> None:
        """幂等关闭死信Producer。"""
        async with self._lifecycle_lock:
            producer = self._producer
            if producer is None:
                return
            try:
                await producer.stop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise EventPublishError(
                    "Could not stop dead-letter producer"
                ) from exc
            self._producer = None

    def _producer_options(self) -> dict[str, object]:
        """构造与业务事件Producer一致的可靠发送参数。"""
        options: dict[str, object] = {
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
    def _serialize(record: DeadLetterRecord) -> bytes:
        """使用Base64保留任意二进制原始消息，避免二次解码失败。"""
        envelope = {
            "dead_letter_id": KafkaDeadLetterPublisher._dead_letter_id(record),
            "source_topic": record.source_topic,
            "source_partition": record.source_partition,
            "source_offset": record.source_offset,
            "key_base64": (
                base64.b64encode(record.key).decode()
                if record.key is not None
                else None
            ),
            "value_base64": base64.b64encode(record.value).decode(),
            "headers": [
                {
                    "name": name,
                    "value_base64": base64.b64encode(value).decode(),
                }
                for name, value in record.headers
            ],
            "reason_code": record.reason_code,
            "reason": record.reason,
            "failed_at": record.failed_at.isoformat(),
        }
        return json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    @staticmethod
    def _dead_letter_id(record: DeadLetterRecord) -> str:
        """使用源坐标生成稳定标识，支持提交失败后的重复死信去重。"""
        return (
            f"{record.source_topic}:"
            f"{record.source_partition}:"
            f"{record.source_offset}"
        )

    @staticmethod
    async def _best_effort_stop(producer: KafkaProducerProtocol) -> None:
        """启动失败后尽力清理Producer，并保留原始异常。"""
        try:
            await producer.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Kafka死信Producer启动失败后的资源清理失败"
            )
