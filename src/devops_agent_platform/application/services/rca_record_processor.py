import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from devops_agent_platform.application.exceptions import (
    MessageContractError,
    PersistenceError,
    ResourceBusyError,
    RuntimeUnavailableError,
)
from devops_agent_platform.application.messages.rca_requested import (
    RCARequestedEventV1,
)
from devops_agent_platform.application.services.rca_requested_handler import (
    RCARequestedHandlingResult,
)
from devops_agent_platform.domain.exceptions import ResourceNotFound
from devops_agent_platform.tools.sanitization import redact_sensitive_text

_MAX_MESSAGE_BYTES = 1024 * 1024


class MessageDisposition(StrEnum):
    """消费记录经过应用处理后的处置类型。"""

    ACK = "ACK"
    RETRY = "RETRY"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass(frozen=True)
class MessageProcessingResult:
    """应用层返回给消息中间件适配器的稳定处置结果。"""

    disposition: MessageDisposition
    reason_code: str | None = None
    reason: str | None = None
    handling: RCARequestedHandlingResult | None = None


class RCARequestedHandlerPort(Protocol):
    """原始记录处理器依赖的最小消息处理能力。"""

    async def handle(
        self,
        event: RCARequestedEventV1,
    ) -> RCARequestedHandlingResult:
        """处理已通过契约校验的RCA消息。"""
        ...


class RCARequestedRecordProcessor:
    """解析原始消息并按异常性质决定确认、重试或死信。"""

    def __init__(self, handler: RCARequestedHandlerPort) -> None:
        self._handler = handler

    async def process(self, value: bytes) -> MessageProcessingResult:
        """处理一条原始消息，不执行Kafka offset操作。"""
        if not isinstance(value, bytes):
            return self._dead_letter(
                "INVALID_VALUE_TYPE",
                "Kafka message value must be bytes",
            )
        if not value or len(value) > _MAX_MESSAGE_BYTES:
            return self._dead_letter(
                "INVALID_MESSAGE_SIZE",
                f"Message size must be between 1 and {_MAX_MESSAGE_BYTES} bytes",
            )

        try:
            envelope = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._dead_letter("INVALID_JSON", self._safe_detail(exc))

        if (
            isinstance(envelope, dict)
            and isinstance(envelope.get("event_type"), str)
            and self._is_safe_foreign_event_type(envelope["event_type"])
            and envelope["event_type"] != "rca.requested"
        ):
            return MessageProcessingResult(
                disposition=MessageDisposition.ACK,
                reason_code="EVENT_NOT_TARGETED",
                reason="Event is not handled by the RCA consumer",
            )

        try:
            event = RCARequestedEventV1.from_envelope(envelope)
            handling = await self._handler.handle(event)
        except MessageContractError as exc:
            return self._dead_letter(exc.code, self._safe_detail(exc))
        except ResourceNotFound as exc:
            # Outbox与WorkflowRun同事务提交，正常消息不应引用缺失记录。
            return self._dead_letter("WORKFLOW_NOT_FOUND", self._safe_detail(exc))
        except (
            PersistenceError,
            ResourceBusyError,
            RuntimeUnavailableError,
        ) as exc:
            return self._retry(exc.code, self._safe_detail(exc))
        except Exception as exc:
            # 未知故障优先保留消息，避免应用缺陷导致不可恢复的数据丢失。
            return self._retry("UNEXPECTED_PROCESSING_ERROR", self._safe_detail(exc))

        return MessageProcessingResult(
            disposition=MessageDisposition.ACK,
            handling=handling,
        )

    @staticmethod
    def _dead_letter(reason_code: str, reason: str) -> MessageProcessingResult:
        """构造不可重试消息结果。"""
        return MessageProcessingResult(
            disposition=MessageDisposition.DEAD_LETTER,
            reason_code=reason_code,
            reason=reason,
        )

    @staticmethod
    def _retry(reason_code: str, reason: str) -> MessageProcessingResult:
        """构造应保留源offset并稍后重试的结果。"""
        return MessageProcessingResult(
            disposition=MessageDisposition.RETRY,
            reason_code=reason_code,
            reason=reason,
        )

    @staticmethod
    def _safe_detail(exc: Exception) -> str:
        """生成单行有限长度错误摘要，避免死信和日志被异常文本放大。"""
        detail = " ".join(str(exc).splitlines()).strip()
        detail = redact_sensitive_text(detail)[0]
        message = type(exc).__name__
        if detail:
            message = f"{message}: {detail}"
        return message[:1024]

    @staticmethod
    def _is_safe_foreign_event_type(event_type: str) -> bool:
        """只有干净的外部事件类型才按共享Topic旁路确认。"""
        return (
            1 <= len(event_type) <= 128
            and event_type == event_type.strip()
            and not any(
                ord(character) < 32 or ord(character) == 127 for character in event_type
            )
        )
