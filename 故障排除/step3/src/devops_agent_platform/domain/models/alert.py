from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import AlertSeverity


@dataclass(frozen=True)
class Alert:
    """从监控系统或 Webhook 接收到的外部告警事实。"""

    alert_id: str
    tenant_id: str
    source: str
    service_name: str
    severity: AlertSeverity
    summary: str
    starts_at: datetime
    fingerprint: str
