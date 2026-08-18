from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.models.workspace import WorkspaceConfig


class WorkspaceRepositoryPort(Protocol):
    async def save(self, workspace: WorkspaceConfig) -> None: ...

    async def get(
        self, tenant_id: str, workspace_id: str
    ) -> WorkspaceConfig | None: ...

    async def list_for_tenant(self, tenant_id: str) -> tuple[WorkspaceConfig, ...]: ...


@dataclass(frozen=True)
class WorkspaceMutation:
    operation_id: str
    workspace: WorkspaceConfig
    expected_revision: int
    idempotency_key_hash: str
    request_hash: str
    requested_by: str
    trace_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class WorkspaceMutationResult:
    operation_id: str
    workspace: WorkspaceConfig
    is_duplicate: bool


class WorkspaceAdminStorePort(Protocol):
    async def get(
        self, tenant_id: str, workspace_id: str
    ) -> WorkspaceConfig | None: ...

    async def list_for_tenant(self, tenant_id: str) -> tuple[WorkspaceConfig, ...]: ...

    async def apply(
        self,
        mutation: WorkspaceMutation,
        audit_event: OutboxEvent,
    ) -> WorkspaceMutationResult: ...
