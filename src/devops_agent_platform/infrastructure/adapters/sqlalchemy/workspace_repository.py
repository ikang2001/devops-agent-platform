from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import AppValidationError, ConflictError
from devops_agent_platform.domain.models.workspace import WorkspaceConfig
from devops_agent_platform.infrastructure.database.mappers.outbox import (
    OutboxEventMapper,
)
from devops_agent_platform.infrastructure.database.models.workspace import (
    WorkspaceOperationRecord,
    WorkspaceRecord,
)
from devops_agent_platform.ports.workspace import (
    WorkspaceMutation,
    WorkspaceMutationResult,
)


class SQLAlchemyWorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, workspace: WorkspaceConfig) -> None:
        record = WorkspaceRecord(
            workspace_id=workspace.workspace_id,
            tenant_id=workspace.tenant_id,
            name=workspace.name,
            prometheus_target=workspace.prometheus_target,
            loki_target=workspace.loki_target,
            tempo_target=workspace.tempo_target,
            knowledge_scope=workspace.knowledge_scope,
            investigation_policy=workspace.investigation_policy,
            allowed_tools_json=json.dumps(workspace.allowed_tools),
            llm_provider_policy=workspace.llm_provider_policy,
            retention_days=workspace.retention_days,
            revision=workspace.revision,
            updated_at=workspace.updated_at,
        )
        try:
            self._session.add(record)
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("workspace persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("could not persist workspace") from exc

    async def get(self, tenant_id: str, workspace_id: str) -> WorkspaceConfig | None:
        statement = select(WorkspaceRecord).where(
            WorkspaceRecord.tenant_id == tenant_id,
            WorkspaceRecord.workspace_id == workspace_id,
        )
        try:
            record = await self._session.scalar(statement)
        except SQLAlchemyError as exc:
            raise PersistenceError("could not load workspace") from exc
        return _to_domain(record) if record is not None else None

    async def list_for_tenant(self, tenant_id: str) -> tuple[WorkspaceConfig, ...]:
        statement = (
            select(WorkspaceRecord)
            .where(WorkspaceRecord.tenant_id == tenant_id)
            .order_by(WorkspaceRecord.workspace_id)
        )
        try:
            records = (await self._session.scalars(statement)).all()
        except SQLAlchemyError as exc:
            raise PersistenceError("could not list workspaces") from exc
        return tuple(_to_domain(record) for record in records)


class SQLAlchemyWorkspaceAdminStore:
    """原子保存 Workspace、幂等结果和不含端点正文的审计事件。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        if not callable(session_factory):
            raise AppValidationError("session_factory must be callable")
        self._session_factory = session_factory

    async def apply(
        self,
        mutation: WorkspaceMutation,
        audit_event: OutboxEvent,
    ) -> WorkspaceMutationResult:
        self._validate_contract(mutation, audit_event)
        try:
            return await self._apply_once(mutation, audit_event)
        except IntegrityError as exc:
            recovered = await self._recover_operation(mutation)
            if recovered is not None:
                return recovered
            raise ConflictError("workspace persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("could not mutate workspace") from exc

    async def get(self, tenant_id: str, workspace_id: str) -> WorkspaceConfig | None:
        try:
            async with self._session_factory() as session:
                record = await session.scalar(
                    select(WorkspaceRecord).where(
                        WorkspaceRecord.tenant_id == tenant_id,
                        WorkspaceRecord.workspace_id == workspace_id,
                    )
                )
        except SQLAlchemyError as exc:
            raise PersistenceError("could not load workspace") from exc
        return _to_domain(record) if record is not None else None

    async def list_for_tenant(self, tenant_id: str) -> tuple[WorkspaceConfig, ...]:
        try:
            async with self._session_factory() as session:
                records = (
                    await session.scalars(
                        select(WorkspaceRecord)
                        .where(WorkspaceRecord.tenant_id == tenant_id)
                        .order_by(WorkspaceRecord.workspace_id)
                    )
                ).all()
        except SQLAlchemyError as exc:
            raise PersistenceError("could not list workspaces") from exc
        return tuple(_to_domain(record) for record in records)

    async def _apply_once(
        self,
        mutation: WorkspaceMutation,
        audit_event: OutboxEvent,
    ) -> WorkspaceMutationResult:
        async with self._session_factory() as session:
            async with session.begin():
                operation = await self._find_operation(
                    session,
                    mutation.workspace.tenant_id,
                    mutation.idempotency_key_hash,
                )
                if operation is not None:
                    return self._existing_result(operation, mutation)
                record = await session.scalar(
                    select(WorkspaceRecord)
                    .where(
                        WorkspaceRecord.tenant_id == mutation.workspace.tenant_id,
                        WorkspaceRecord.workspace_id == mutation.workspace.workspace_id,
                    )
                    .with_for_update()
                    .limit(1)
                )
                if record is None:
                    if mutation.expected_revision != 0:
                        raise ConflictError("workspace revision conflict")
                    record = _to_record(mutation.workspace)
                    session.add(record)
                else:
                    if record.revision != mutation.expected_revision:
                        raise ConflictError("workspace revision conflict")
                    _copy_workspace(record, mutation.workspace)
                result_json = json.dumps(
                    _workspace_payload(mutation.workspace),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                operation = WorkspaceOperationRecord(
                    operation_id=mutation.operation_id,
                    tenant_id=mutation.workspace.tenant_id,
                    workspace_id=mutation.workspace.workspace_id,
                    idempotency_key_hash=mutation.idempotency_key_hash,
                    request_hash=mutation.request_hash,
                    result_json=result_json,
                    result_revision=mutation.workspace.revision,
                    requested_by=mutation.requested_by,
                    trace_id=mutation.trace_id,
                    occurred_at=mutation.occurred_at,
                )
                session.add(operation)
                session.add(OutboxEventMapper.to_record(audit_event))
                await session.flush()
                return WorkspaceMutationResult(
                    operation_id=operation.operation_id,
                    workspace=mutation.workspace,
                    is_duplicate=False,
                )

    async def _recover_operation(
        self,
        mutation: WorkspaceMutation,
    ) -> WorkspaceMutationResult | None:
        try:
            async with self._session_factory() as session:
                operation = await self._find_operation(
                    session,
                    mutation.workspace.tenant_id,
                    mutation.idempotency_key_hash,
                )
        except SQLAlchemyError as exc:
            raise PersistenceError("could not recover workspace operation") from exc
        if operation is None:
            return None
        return self._existing_result(operation, mutation)

    @staticmethod
    async def _find_operation(
        session: AsyncSession,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> WorkspaceOperationRecord | None:
        return await session.scalar(
            select(WorkspaceOperationRecord)
            .where(
                WorkspaceOperationRecord.tenant_id == tenant_id,
                WorkspaceOperationRecord.idempotency_key_hash == idempotency_key_hash,
            )
            .limit(1)
        )

    @staticmethod
    def _existing_result(
        operation: WorkspaceOperationRecord,
        mutation: WorkspaceMutation,
    ) -> WorkspaceMutationResult:
        if operation.request_hash != mutation.request_hash:
            raise ConflictError(
                "idempotency key was used for another workspace request"
            )
        try:
            workspace = _workspace_from_payload(json.loads(operation.result_json))
        except (AppValidationError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                "stored workspace operation result is invalid"
            ) from exc
        return WorkspaceMutationResult(
            operation_id=operation.operation_id,
            workspace=workspace,
            is_duplicate=True,
        )

    @staticmethod
    def _validate_contract(
        mutation: WorkspaceMutation,
        audit_event: OutboxEvent,
    ) -> None:
        if not isinstance(mutation, WorkspaceMutation):
            raise AppValidationError("mutation must be a WorkspaceMutation")
        workspace = mutation.workspace
        if (
            workspace.revision != mutation.expected_revision + 1
            or workspace.updated_at != mutation.occurred_at
            or audit_event.tenant_id != workspace.tenant_id
            or audit_event.aggregate_id != mutation.operation_id
            or audit_event.trace_id != mutation.trace_id
            or audit_event.occurred_at != mutation.occurred_at
        ):
            raise AppValidationError("workspace mutation and audit event do not match")


def _to_domain(record: WorkspaceRecord) -> WorkspaceConfig:
    try:
        allowed_tools = tuple(json.loads(record.allowed_tools_json))
    except (TypeError, ValueError) as exc:
        raise PersistenceError("stored workspace allowed_tools is invalid") from exc
    return WorkspaceConfig(
        workspace_id=record.workspace_id,
        tenant_id=record.tenant_id,
        name=record.name,
        prometheus_target=record.prometheus_target,
        loki_target=record.loki_target,
        tempo_target=record.tempo_target,
        knowledge_scope=record.knowledge_scope,
        investigation_policy=record.investigation_policy,
        allowed_tools=allowed_tools,
        llm_provider_policy=record.llm_provider_policy,
        retention_days=record.retention_days,
        revision=record.revision,
        updated_at=record.updated_at,
    )


def _to_record(workspace: WorkspaceConfig) -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_id=workspace.workspace_id,
        tenant_id=workspace.tenant_id,
        name=workspace.name,
        prometheus_target=workspace.prometheus_target,
        loki_target=workspace.loki_target,
        tempo_target=workspace.tempo_target,
        knowledge_scope=workspace.knowledge_scope,
        investigation_policy=workspace.investigation_policy,
        allowed_tools_json=json.dumps(workspace.allowed_tools),
        llm_provider_policy=workspace.llm_provider_policy,
        retention_days=workspace.retention_days,
        revision=workspace.revision,
        updated_at=workspace.updated_at,
    )


def _copy_workspace(record: WorkspaceRecord, workspace: WorkspaceConfig) -> None:
    source = _to_record(workspace)
    for field_name in (
        "name",
        "prometheus_target",
        "loki_target",
        "tempo_target",
        "knowledge_scope",
        "investigation_policy",
        "allowed_tools_json",
        "llm_provider_policy",
        "retention_days",
        "revision",
        "updated_at",
    ):
        setattr(record, field_name, getattr(source, field_name))


def _workspace_payload(workspace: WorkspaceConfig) -> dict[str, Any]:
    return {
        "workspace_id": workspace.workspace_id,
        "tenant_id": workspace.tenant_id,
        "name": workspace.name,
        "prometheus_target": workspace.prometheus_target,
        "loki_target": workspace.loki_target,
        "tempo_target": workspace.tempo_target,
        "knowledge_scope": workspace.knowledge_scope,
        "investigation_policy": workspace.investigation_policy,
        "allowed_tools": workspace.allowed_tools,
        "llm_provider_policy": workspace.llm_provider_policy,
        "retention_days": workspace.retention_days,
        "revision": workspace.revision,
        "updated_at": workspace.updated_at.isoformat(),
    }


def _workspace_from_payload(value: object) -> WorkspaceConfig:
    if not isinstance(value, dict):
        raise ValueError("workspace payload must be an object")
    payload = dict(value)
    payload["allowed_tools"] = tuple(payload["allowed_tools"])
    payload["updated_at"] = datetime.fromisoformat(payload["updated_at"])
    return WorkspaceConfig(**payload)
