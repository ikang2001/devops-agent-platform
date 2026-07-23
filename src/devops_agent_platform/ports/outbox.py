from datetime import datetime
from typing import Protocol

from devops_agent_platform.application.events import (
    ClaimedOutboxEvent,
    OutboxEvent,
)


class OutboxRepositoryPort(Protocol):
    """应用服务写入事务型 Outbox 所依赖的端口。"""

    async def add(self, event: OutboxEvent) -> None:
        """把待发布事件加入当前数据库事务。"""
        ...


class OutboxDispatchStorePort(Protocol):
    """Outbox 发布器执行短事务状态流转所依赖的端口。"""

    async def claim_batch(
        self,
        worker_id: str,
        now: datetime,
        locked_until: datetime,
        limit: int,
        max_attempts: int,
    ) -> list[ClaimedOutboxEvent]:
        """抢占一批到期事件并提交租约。"""
        ...

    async def mark_published(
        self,
        event_id: str,
        worker_id: str,
        published_at: datetime,
    ) -> None:
        """把当前Worker持有的事件标记为已发布。"""
        ...

    async def mark_retry(
        self,
        event_id: str,
        worker_id: str,
        available_at: datetime,
        last_error: str,
    ) -> None:
        """释放租约并把事件重新排入待发布队列。"""
        ...

    async def mark_failed(
        self,
        event_id: str,
        worker_id: str,
        last_error: str,
    ) -> None:
        """把达到最大尝试次数的事件标记为终态失败。"""
        ...
