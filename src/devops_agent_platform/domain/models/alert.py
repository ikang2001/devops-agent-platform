from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import AlertSeverity
from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class Alert:
    """从监控系统或 Webhook 接收到的不可变告警事实。"""

    alert_id: str
    tenant_id: str
    source: str
    service_name: str
    severity: AlertSeverity
    summary: str
    starts_at: datetime
    fingerprint: str
    external_event_id: str
    incident_id: str | None = None

    def __post_init__(self) -> None:
        """校验告警事实，阻止内部调用绕过 HTTP DTO 写入脏数据。"""
        self._validate_text("alert_id", self.alert_id, 64)
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("source", self.source, 128)
        self._validate_text("service_name", self.service_name, 256)
        self._validate_text("summary", self.summary, 2048)
        self._validate_text("fingerprint", self.fingerprint, 256)
        self._validate_text("external_event_id", self.external_event_id, 256)
        if self.incident_id is not None:
            self._validate_text("incident_id", self.incident_id, 64)

        if not isinstance(self.severity, AlertSeverity):
            raise AppValidationError("severity must be an AlertSeverity")
        if not isinstance(self.starts_at, datetime):
            raise AppValidationError("starts_at must be a datetime")
        if self.starts_at.tzinfo is None or self.starts_at.utcoffset() is None:
            raise AppValidationError("starts_at must include timezone information")

    @staticmethod
    def _validate_text(field_name: str, value: str, max_length: int) -> None:
        """校验必填文本类型、长度和首尾空白。"""
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
