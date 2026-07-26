from dataclasses import dataclass
from typing import Protocol

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class NotificationMessage:
    """从可信事故和 RCA 报告派生的有限通知消息。"""

    tenant_id: str
    workflow_run_id: str
    incident_id: str
    service_name: str
    severity: str
    title: str
    summary: str
    recommendations: tuple[str, ...]
    idempotency_key: str
    trace_id: str

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("incident_id", self.incident_id, 64),
            ("service_name", self.service_name, 256),
            ("severity", self.severity, 16),
            ("title", self.title, 256),
            ("summary", self.summary, 4096),
            ("idempotency_key", self.idempotency_key, 256),
            ("trace_id", self.trace_id, 128),
        ):
            _validate_text(name, value, maximum, multiline=name == "summary")
        if (
            not isinstance(self.recommendations, tuple)
            or len(self.recommendations) > 20
        ):
            raise AppValidationError("recommendations is invalid")
        for item in self.recommendations:
            _validate_text("recommendation", item, 1024)


@dataclass(frozen=True)
class NotificationDeliveryOutcome:
    """供应商无关的通知投递结果。"""

    provider: str
    external_reference: str


class NotificationGatewayPort(Protocol):
    """按目标供应商投递可信通知的端口。"""

    async def send(
        self,
        target_system: str,
        message: NotificationMessage,
    ) -> NotificationDeliveryOutcome:
        """投递消息或抛出稳定依赖异常。"""
        ...


def _validate_text(
    name: str,
    value: str,
    maximum: int,
    *,
    multiline: bool = False,
) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(
            (ord(character) < 32 and not (multiline and character == "\n"))
            or ord(character) == 127
            for character in value
        )
    ):
        raise AppValidationError(f"{name} is invalid")
