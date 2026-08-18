import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from devops_agent_platform.domain.enums import ChangeEventStatus, ChangeType
from devops_agent_platform.domain.exceptions import AppValidationError

MAX_CHANGE_METADATA_BYTES = 16 * 1024


@dataclass(frozen=True)
class ChangeEvent:
    """可关联到 RCA 时间窗口的变更事实。"""

    change_event_id: str
    tenant_id: str
    source: str
    external_event_id: str
    service_name: str
    resource_type: str
    resource_id: str
    change_type: ChangeType
    status: ChangeEventStatus
    version_before: str | None
    version_after: str | None
    operator_id: str | None
    summary: str
    metadata_json: str
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    request_hash: str

    def __post_init__(self) -> None:
        """在领域边界校验变更身份、时间线和有限结构化元数据。"""
        self._validate_text("change_event_id", self.change_event_id, 64)
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("source", self.source, 128)
        self._validate_text("external_event_id", self.external_event_id, 256)
        self._validate_text("service_name", self.service_name, 256)
        self._validate_text("resource_type", self.resource_type, 128)
        self._validate_text("resource_id", self.resource_id, 256)
        self._validate_optional_text("version_before", self.version_before, 256)
        self._validate_optional_text("version_after", self.version_after, 256)
        self._validate_optional_text("operator_id", self.operator_id, 128)
        self._validate_text("summary", self.summary, 4096)

        if not isinstance(self.change_type, ChangeType):
            raise AppValidationError("change_type must be a ChangeType")
        if not isinstance(self.status, ChangeEventStatus):
            raise AppValidationError("status must be a ChangeEventStatus")

        self._validate_timestamp("started_at", self.started_at)
        self._validate_timestamp("created_at", self.created_at)
        if self.completed_at is not None:
            self._validate_timestamp("completed_at", self.completed_at)
            if self.completed_at < self.started_at:
                raise AppValidationError("completed_at must not precede started_at")
        self._validate_metadata(self.metadata_json)
        self._validate_request_hash(self.request_hash)

    @staticmethod
    def _validate_text(field_name: str, value: str, max_length: int) -> None:
        """校验必填文本，避免空值、控制字符和边界空白进入索引。"""
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

    @classmethod
    def _validate_optional_text(
        cls,
        field_name: str,
        value: str | None,
        max_length: int,
    ) -> None:
        """校验允许缺省的版本或操作者文本。"""
        if value is not None:
            cls._validate_text(field_name, value, max_length)

    @staticmethod
    def _validate_timestamp(field_name: str, value: datetime) -> None:
        """所有时间必须明确时区，避免跨环境比较发生漂移。"""
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(f"{field_name} must include timezone information")

    @staticmethod
    def _validate_metadata(metadata_json: str) -> None:
        """校验元数据是有限、规范化的 JSON 对象，不接受 NaN/Infinity。"""
        if not isinstance(metadata_json, str) or not metadata_json:
            raise AppValidationError("metadata_json must be a non-empty string")
        if len(metadata_json.encode("utf-8")) > MAX_CHANGE_METADATA_BYTES:
            raise AppValidationError("metadata_json is too large")
        try:
            payload = json.loads(
                metadata_json,
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError) as exc:
            raise AppValidationError("metadata_json must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise AppValidationError("metadata_json must be a JSON object")
        try:
            canonical = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise AppValidationError("metadata_json must be JSON serializable") from exc
        if canonical != metadata_json:
            raise AppValidationError("metadata_json must be canonical JSON")

    @staticmethod
    def _validate_request_hash(request_hash: str) -> None:
        """request_hash 用于区分同一幂等键下的真实重试与误复用。"""
        if (
            not isinstance(request_hash, str)
            or len(request_hash) != 64
            or any(character not in "0123456789abcdef" for character in request_hash)
        ):
            raise AppValidationError("request_hash must be a SHA-256 hex digest")


def build_change_metadata(
    payload: Mapping[str, Any],
    *,
    max_bytes: int = MAX_CHANGE_METADATA_BYTES,
) -> str:
    """将变更元数据编码为稳定 JSON，供应用服务和测试共享。"""
    if not isinstance(payload, Mapping):
        raise AppValidationError("payload must be a mapping")
    try:
        metadata_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AppValidationError("payload must be JSON serializable") from exc
    if len(metadata_json.encode("utf-8")) > max_bytes:
        raise AppValidationError("payload is too large")
    return metadata_json


def _reject_json_constant(value: str) -> None:
    """拒绝 JSON 标准之外的数值常量。"""
    raise ValueError(f"invalid JSON constant: {value}")
