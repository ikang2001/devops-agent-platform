from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident


class StubAlertRepository:
    """不做持久化的告警仓储占位实现。

    该类不能模拟生产持久化，也不能返回伪造业务数据。
    """

    async def save(self, alert: Alert) -> None:
        raise NotImplementedInSkeleton("StubAlertRepository is a Step 3 placeholder")


class StubIncidentRepository:
    """不做持久化的事故仓储占位实现。"""

    async def save(self, incident: Incident) -> None:
        raise NotImplementedInSkeleton("StubIncidentRepository is a Step 3 placeholder")

    async def get_by_id(self, incident_id: str, tenant_id: str) -> Incident | None:
        raise NotImplementedInSkeleton("StubIncidentRepository is a Step 3 placeholder")
