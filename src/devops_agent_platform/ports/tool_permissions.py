from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.enums import ToolPermissionChangeAction


@dataclass(frozen=True)
class ToolPermissionMutation:
    """应用服务交给事务 Store 的完整权限变更参数。"""

    operation_id: str
    new_grant_id: str | None
    tenant_id: str
    operator_id: str
    permission_tags: tuple[str, ...]
    expires_at: datetime | None
    expected_version: int
    idempotency_key_hash: str
    request_hash: str
    requested_by: str
    trace_id: str
    action: ToolPermissionChangeAction
    occurred_at: datetime


@dataclass(frozen=True)
class ToolPermissionMutationResult:
    """权限状态和审计事件原子提交后的结果。"""

    operation_id: str
    grant_id: str
    tenant_id: str
    operator_id: str
    action: ToolPermissionChangeAction
    version: int
    trace_id: str
    is_duplicate: bool


@dataclass(frozen=True)
class ToolPermissionSnapshot:
    """管理端读取的当前权限状态快照。"""

    tenant_id: str
    operator_id: str
    grant_id: str | None
    version: int
    permission_tags: tuple[str, ...]
    expires_at: datetime | None
    active: bool


class ToolPermissionAdminStorePort(Protocol):
    """原子执行权限状态、幂等记录和Outbox写入的管理端口。"""

    async def get_current(
        self,
        tenant_id: str,
        operator_id: str,
    ) -> ToolPermissionSnapshot:
        """读取当前权限快照；没有活动授权时仍返回最新状态版本。"""
        ...

    async def apply(
        self,
        mutation: ToolPermissionMutation,
        audit_event: OutboxEvent,
    ) -> ToolPermissionMutationResult:
        """提交权限变更；相同幂等请求返回原结果。"""
        ...
