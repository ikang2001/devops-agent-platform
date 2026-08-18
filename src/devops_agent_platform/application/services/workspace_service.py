from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256

from devops_agent_platform.application.commands.workspaces import (
    UpsertWorkspaceCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.workspace import WorkspaceConfig
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.workspace import (
    WorkspaceAdminStorePort,
    WorkspaceMutation,
    WorkspaceRepositoryPort,
)
from devops_agent_platform.tools.sanitization import redact_sensitive_text

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class WorkspaceChangeResult:
    operation_id: str
    workspace: WorkspaceConfig
    trace_id: str
    is_duplicate: bool

    def to_dict(self) -> dict:
        payload = _workspace_payload(self.workspace)
        payload.update(
            {
                "operation_id": self.operation_id,
                "trace_id": self.trace_id,
                "is_duplicate": self.is_duplicate,
            }
        )
        return payload


class WorkspaceService:
    def __init__(
        self,
        repository: WorkspaceRepositoryPort,
        *,
        admin_store: WorkspaceAdminStorePort | None = None,
        identifier_generator: IdentifierGeneratorPort | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._admin_store = admin_store
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create_or_update(self, workspace: WorkspaceConfig) -> WorkspaceConfig:
        """保留领域级仓储用法；HTTP 管理写入必须走 upsert。"""
        existing = await self._repository.get(
            workspace.tenant_id, workspace.workspace_id
        )
        if existing is not None and existing.name != workspace.name:
            raise ConflictError("workspace id already belongs to another workspace")
        await self._repository.save(workspace)
        return workspace

    async def upsert(self, command: UpsertWorkspaceCommand) -> WorkspaceChangeResult:
        if self._admin_store is None or self._identifier_generator is None:
            raise AppValidationError("workspace administration is not configured")
        now = self._now()
        safe_name = redact_sensitive_text(command.name)[0].strip()
        if not safe_name:
            raise AppValidationError("workspace name is empty after sanitization")
        workspace = WorkspaceConfig(
            workspace_id=command.workspace_id,
            tenant_id=command.tenant_id,
            name=safe_name,
            prometheus_target=command.prometheus_target,
            loki_target=command.loki_target,
            tempo_target=command.tempo_target,
            knowledge_scope=command.knowledge_scope,
            investigation_policy=command.investigation_policy,
            allowed_tools=command.allowed_tools,
            llm_provider_policy=command.llm_provider_policy,
            retention_days=command.retention_days,
            revision=command.expected_revision + 1,
            updated_at=now,
        )
        operation_id = self._identifier_generator.new_workspace_operation_id()
        content_hash = _canonical_hash(
            _workspace_payload(workspace, include_revision=False)
        )
        mutation = WorkspaceMutation(
            operation_id=operation_id,
            workspace=workspace,
            expected_revision=command.expected_revision,
            idempotency_key_hash=_hash_text(command.idempotency_key),
            request_hash=_canonical_hash(
                {
                    "tenant_id": command.tenant_id,
                    "workspace_id": command.workspace_id,
                    "expected_revision": command.expected_revision,
                    "requested_by": command.requested_by,
                    "content_sha256": content_hash,
                }
            ),
            requested_by=command.requested_by,
            trace_id=command.trace_id,
            occurred_at=now,
        )
        stored = await self._admin_store.apply(
            mutation,
            OutboxEvent(
                event_id=self._identifier_generator.new_event_id(),
                tenant_id=command.tenant_id,
                aggregate_type="WorkspaceOperation",
                aggregate_id=operation_id,
                event_type="workspace.configuration.updated",
                schema_version=1,
                payload={
                    "operation_id": operation_id,
                    "workspace_id": command.workspace_id,
                    "configuration_sha256": content_hash,
                    "result_revision": workspace.revision,
                    "requested_by": command.requested_by,
                    "occurred_at": now.isoformat(),
                },
                occurred_at=now,
                trace_id=command.trace_id,
            ),
        )
        return WorkspaceChangeResult(
            operation_id=stored.operation_id,
            workspace=stored.workspace,
            trace_id=command.trace_id,
            is_duplicate=stored.is_duplicate,
        )

    async def get(self, tenant_id: str, workspace_id: str) -> WorkspaceConfig:
        workspace = await self._repository.get(tenant_id, workspace_id)
        if workspace is None:
            raise ResourceNotFound("workspace not found")
        return workspace

    async def list(self, tenant_id: str) -> tuple[WorkspaceConfig, ...]:
        return await self._repository.list_for_tenant(tenant_id)

    def _now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise AppValidationError("clock must return a timezone-aware datetime")
        return now.astimezone(UTC)


def _workspace_payload(
    workspace: WorkspaceConfig,
    *,
    include_revision: bool = True,
) -> dict:
    payload = asdict(workspace)
    payload["updated_at"] = workspace.updated_at.isoformat()
    if not include_revision:
        payload.pop("revision")
        payload.pop("updated_at")
    return payload


def _canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _hash_text(value: str) -> str:
    return sha256(value.encode()).hexdigest()
