from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import AlertSeverity, IncidentStatus


@dataclass
class Incident:
    """生产事故聚合根骨架。"""

    incident_id: str
    tenant_id: str
    service_name: str
    severity: AlertSeverity
    status: IncidentStatus
    title: str
    created_at: datetime
    updated_at: datetime

    def mark_analyzing(self) -> None:
        """将事故推进到分析中状态。

        Step 3 只定义状态流转入口。具体业务状态规则会在后续步骤实现。
        """
        raise NotImplementedError("Incident state rules are not implemented in Step 3")

    def mark_resolved(self) -> None:
        """在人工确认后将事故推进到已解决状态。"""
        raise NotImplementedError("Incident state rules are not implemented in Step 3")
