from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from devops_agent_platform.domain.enums import ChangeEventStatus, ChangeType
from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class ReceiveChangeEventCommand:
    """接收外部变更事件用例的应用层入参。"""

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
    metadata: Mapping[str, Any]
    started_at: datetime
    completed_at: datetime | None
    trace_id: str

    def __post_init__(self) -> None:
        """校验内部调用也不能绕过的变更接入边界。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("source", self.source, 128),
            ("external_event_id", self.external_event_id, 256),
            ("service_name", self.service_name, 256),
            ("resource_type", self.resource_type, 128),
            ("resource_id", self.resource_id, 256),
            ("trace_id", self.trace_id, 128),
        ):
            _validate_identifier_text(field_name, value, maximum)
        for field_name, value, maximum in (
            ("version_before", self.version_before, 256),
            ("version_after", self.version_after, 256),
            ("operator_id", self.operator_id, 128),
        ):
            if value is not None:
                _validate_identifier_text(field_name, value, maximum)

        _validate_summary(self.summary)
        if not isinstance(self.metadata, Mapping):
            raise AppValidationError("metadata must be a mapping")
        if not isinstance(self.change_type, ChangeType):
            raise AppValidationError("change_type must be a ChangeType")
        if not isinstance(self.status, ChangeEventStatus):
            raise AppValidationError("status must be a ChangeEventStatus")
        _validate_timestamp("started_at", self.started_at)
        if self.completed_at is not None:
            _validate_timestamp("completed_at", self.completed_at)
            if self.completed_at < self.started_at:
                raise AppValidationError("completed_at must not precede started_at")


def _validate_identifier_text(
    field_name: str,
    value: str,
    maximum: int,
) -> None:
    """拒绝空白、超长和包含空白字符的身份字段。"""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(character.isspace() or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _validate_summary(value: str) -> None:
    """允许多行变更摘要进入脱敏步骤，但拒绝其它控制字符。"""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 4096
        or value != value.strip()
        or any(
            (ord(character) < 32 and character != "\n") or ord(character) == 127
            for character in value
        )
    ):
        raise AppValidationError("summary is invalid")


def _validate_timestamp(field_name: str, value: datetime) -> None:
    """要求变更时间明确时区。"""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise AppValidationError(f"{field_name} must include timezone information")
