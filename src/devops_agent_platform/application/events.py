import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from devops_agent_platform.domain.exceptions import AppValidationError

_MAX_PAYLOAD_BYTES = 64 * 1024


@dataclass(frozen=True)
class OutboxEvent:
    """需要与业务数据原子持久化的应用事件。"""

    event_id: str
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    schema_version: int
    payload: dict[str, Any]
    occurred_at: datetime
    trace_id: str

    def __post_init__(self) -> None:
        """校验事件元数据和 JSON Payload，阻止坏消息进入 Outbox。"""
        self._validate_text("event_id", self.event_id, 64)
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("aggregate_type", self.aggregate_type, 64)
        self._validate_text("aggregate_id", self.aggregate_id, 64)
        self._validate_text("event_type", self.event_type, 128)
        self._validate_text("trace_id", self.trace_id, 128)
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise AppValidationError("schema_version must be a positive integer")
        if not isinstance(self.payload, dict):
            raise AppValidationError("payload must be a dictionary")
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise AppValidationError("occurred_at must include timezone information")

        try:
            encoded_payload = json.dumps(
                self.payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError) as exc:
            raise AppValidationError("payload must be JSON serializable") from exc
        if len(encoded_payload) > _MAX_PAYLOAD_BYTES:
            raise AppValidationError(
                f"payload must not exceed {_MAX_PAYLOAD_BYTES} bytes"
            )

    @staticmethod
    def _validate_text(field_name: str, value: str, max_length: int) -> None:
        """校验事件索引字段，避免空键、超长和控制字符污染消息。"""
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


@dataclass(frozen=True)
class ClaimedOutboxEvent:
    """发布器已获得租约的事件快照。"""

    event: OutboxEvent
    attempts: int

    def __post_init__(self) -> None:
        """保证尝试次数与已抢占状态一致。"""
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise AppValidationError("attempts must be an integer")
        if self.attempts < 1:
            raise AppValidationError("attempts must be greater than or equal to 1")
