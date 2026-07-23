from datetime import datetime
from typing import Protocol

from devops_agent_platform.domain.enums import IncidentStatus
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident


class AlertRepositoryPort(Protocol):
    """告警记录持久化端口。"""

    async def save(self, alert: Alert) -> None:
        """持久化一条告警。

        具体实现必须处理数据库异常，并向上抛出应用异常，不能泄露基础设施细节。
        """
        ...

    async def get_by_external_event_id(
        self,
        tenant_id: str,
        source: str,
        external_event_id: str,
    ) -> Alert | None:
        """按租户、来源和上游事件 ID 查询已接收告警。"""
        ...


class IncidentRepositoryPort(Protocol):
    """事故聚合持久化端口。"""

    async def save(self, incident: Incident) -> None:
        """持久化一个事故聚合。"""
        ...

    async def get_by_id(self, incident_id: str, tenant_id: str) -> Incident | None:
        """按租户隔离的事故标识加载单个事故。"""
        ...

    async def list_page(
        self,
        tenant_id: str,
        statuses: frozenset[IncidentStatus],
        *,
        before_updated_at: datetime | None,
        before_incident_id: str | None,
        limit: int,
    ) -> list[Incident]:
        """按更新时间和事故 ID 倒序读取一个 Keyset 页面。"""
        ...

    async def find_candidates(
        self,
        tenant_id: str,
        service_name: str,
        statuses: frozenset[IncidentStatus],
        created_before: datetime,
        updated_after: datetime,
        limit: int = 50,
    ) -> list[Incident]:
        """按租户、服务、活动状态和时间范围加载有限候选事故。"""
        ...
