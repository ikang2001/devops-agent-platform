from datetime import datetime
from typing import Protocol

from devops_agent_platform.domain.models.change_event import ChangeEvent


class ChangeEventRepositoryPort(Protocol):
    """变更事件持久化与受控时间窗口查询端口。"""

    async def save(self, change_event: ChangeEvent) -> None:
        """保存一条变更事实，由外部 Unit of Work 负责提交。"""
        ...

    async def get_by_id(
        self,
        change_event_id: str,
        tenant_id: str,
    ) -> ChangeEvent | None:
        """按租户隔离读取单条变更事实。"""
        ...

    async def get_by_external_event_id(
        self,
        tenant_id: str,
        source: str,
        external_event_id: str,
    ) -> ChangeEvent | None:
        """按完整幂等键读取单条变更事实。"""
        ...

    async def list_for_service(
        self,
        tenant_id: str,
        service_name: str,
        *,
        limit: int = 50,
    ) -> list[ChangeEvent]:
        """按服务倒序读取有限变更记录。"""
        ...

    async def list_in_time_window(
        self,
        tenant_id: str,
        service_name: str,
        started_at_from: datetime,
        started_at_to: datetime,
        *,
        limit: int = 50,
    ) -> list[ChangeEvent]:
        """按服务和闭区间时间窗口读取有限变更记录。"""
        ...
