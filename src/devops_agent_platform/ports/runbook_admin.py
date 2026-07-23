from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.enums import (
    RunbookChangeAction,
    RunbookStatus,
)


@dataclass(frozen=True)
class RunbookMutation:
    """应用服务交给事务 Store 的完整 Runbook 变更参数。"""

    operation_id: str
    new_runbook_id: str | None
    tenant_id: str
    runbook_key: str
    version: str
    service_name: str | None
    title: str | None
    summary: str | None
    priority: int | None
    steps: tuple[str, ...]
    tags: tuple[str, ...]
    expected_revision: int
    idempotency_key_hash: str
    request_hash: str
    content_hash: str | None
    requested_by: str
    trace_id: str
    action: RunbookChangeAction
    occurred_at: datetime


@dataclass(frozen=True)
class RunbookMutationResult:
    """Runbook 状态和审计事件原子提交后的稳定结果。"""

    operation_id: str
    runbook_id: str
    tenant_id: str
    runbook_key: str
    version: str
    status: RunbookStatus
    revision: int
    trace_id: str
    is_duplicate: bool


class RunbookAdminStorePort(Protocol):
    """原子执行 Runbook 状态、幂等记录和 Outbox 写入的端口。"""

    async def apply(
        self,
        mutation: RunbookMutation,
        audit_event: OutboxEvent,
    ) -> RunbookMutationResult:
        """提交管理变更；相同幂等请求返回原结果。"""
        ...
