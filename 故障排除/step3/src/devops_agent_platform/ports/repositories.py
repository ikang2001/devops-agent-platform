from typing import Protocol

from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident


class AlertRepositoryPort(Protocol):
    """告警记录持久化端口。"""

    async def save(self, alert: Alert) -> None:
        """持久化一条告警。

        具体实现必须处理数据库异常，并向上抛出应用异常，不能泄露基础设施细节。
        """
        ...


class IncidentRepositoryPort(Protocol):
    """事故聚合持久化端口。"""

    async def save(self, incident: Incident) -> None:
        """持久化一个事故聚合。"""
        ...

    async def get_by_id(self, incident_id: str, tenant_id: str) -> Incident | None:
        """按租户隔离的事故标识加载单个事故。"""
        ...
