from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import AlertSeverity


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
    trace_id: str
