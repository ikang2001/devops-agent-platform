from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import AlertSeverity
from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class ReceiveAlertCommand:
    """接收外部告警用例的应用层入参。

    该对象不能包含 FastAPI 的 request/response 类型。接口适配器负责把
    HTTP DTO 转换为 Command，保证 application 层不感知 Web 框架。
    """

    tenant_id: str
    source: str
    service_name: str
    severity: AlertSeverity
    summary: str
    starts_at: datetime
    fingerprint: str
    external_event_id: str
    trace_id: str
    environment: str = "default"
    alert_type: str = "generic"
    labels: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """校验告警接入身份和摘要，避免内部调用绕过 HTTP DTO。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("source", self.source, 128),
            ("service_name", self.service_name, 256),
            ("fingerprint", self.fingerprint, 256),
            ("external_event_id", self.external_event_id, 256),
            ("trace_id", self.trace_id, 128),
        ):
            _validate_identifier_text(field_name, value, maximum)
        _validate_summary(self.summary)
        _validate_identifier_text("environment", self.environment, 64)
        _validate_identifier_text("alert_type", self.alert_type, 128)
        if not isinstance(self.labels, tuple) or len(self.labels) > 64:
            raise AppValidationError("labels must be a tuple with at most 64 items")
        for key, value in self.labels:
            _validate_identifier_text("label", key, 128)
            if not isinstance(value, str) or not 1 <= len(value) <= 512:
                raise AppValidationError("label value is invalid")
        if not isinstance(self.severity, AlertSeverity):
            raise AppValidationError("severity must be an AlertSeverity")
        if (
            not isinstance(self.starts_at, datetime)
            or self.starts_at.tzinfo is None
            or self.starts_at.utcoffset() is None
        ):
            raise AppValidationError("starts_at must include timezone information")


def _validate_identifier_text(
    field_name: str,
    value: str,
    maximum: int,
) -> None:
    """校验身份字段，拒绝空白、超长和任何空白字符。"""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(character.isspace() or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _validate_summary(value: str) -> None:
    """告警摘要可多行，但不能为空、超长或包含不可见控制字符。"""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2048
        or value != value.strip()
        or any(
            (ord(character) < 32 and character != "\n") or ord(character) == 127
            for character in value
        )
    ):
        raise AppValidationError("summary is invalid")
