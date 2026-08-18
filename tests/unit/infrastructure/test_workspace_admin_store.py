from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from devops_agent_platform.application.commands.workspaces import UpsertWorkspaceCommand
from devops_agent_platform.application.services.workspace_service import (
    WorkspaceService,
)
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyWorkspaceAdminStore,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)
from devops_agent_platform.infrastructure.identifiers import UUIDIdentifierGenerator


@pytest.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'workspace-admin.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def command(
    *,
    tenant_id: str = "tenant-a",
    workspace_id: str = "shop",
    revision: int = 0,
    idempotency_key: str = "workspace-idem-001",
) -> UpsertWorkspaceCommand:
    return UpsertWorkspaceCommand(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        name="MiniShop",
        prometheus_target="https://prometheus.example",
        loki_target="https://loki.example",
        tempo_target="https://tempo.example",
        knowledge_scope="tenant",
        investigation_policy="bounded_dynamic_v1",
        allowed_tools=("metrics.query@v1", "logs.query@v1"),
        llm_provider_policy="approved-primary",
        retention_days=30,
        expected_revision=revision,
        idempotency_key=idempotency_key,
        requested_by="admin-001",
        trace_id="trace-workspace-001",
    )


@pytest.mark.asyncio
async def test_workspace_admin_is_idempotent_audited_and_tenant_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SQLAlchemyWorkspaceAdminStore(session_factory)
    service = WorkspaceService(
        store,
        admin_store=store,
        identifier_generator=UUIDIdentifierGenerator(),
        clock=lambda: datetime(2026, 8, 18, tzinfo=UTC),
    )

    created = await service.upsert(command())
    duplicate = await service.upsert(command())
    other_tenant = await service.upsert(
        command(
            tenant_id="tenant-b",
            idempotency_key="workspace-idem-tenant-b",
        )
    )

    assert created.workspace.revision == 1
    assert duplicate.operation_id == created.operation_id
    assert duplicate.is_duplicate is True
    assert other_tenant.workspace.workspace_id == "shop"
    assert await service.get("tenant-a", "shop") == created.workspace
    assert await service.get("tenant-b", "shop") == other_tenant.workspace
    async with session_factory() as session:
        events = (await session.scalars(select(OutboxEventRecord))).all()
    assert len(events) == 2
    serialized_payload = str(events[0].payload)
    assert "prometheus.example" not in serialized_payload
    assert "configuration_sha256" in serialized_payload


@pytest.mark.asyncio
async def test_workspace_admin_enforces_revision_and_idempotency_request_hash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SQLAlchemyWorkspaceAdminStore(session_factory)
    service = WorkspaceService(
        store,
        admin_store=store,
        identifier_generator=UUIDIdentifierGenerator(),
    )
    await service.upsert(command())

    with pytest.raises(ConflictError, match="revision"):
        await service.upsert(command(idempotency_key="new-key-with-stale-revision"))
    changed = command()
    changed = UpsertWorkspaceCommand(
        **{**changed.__dict__, "name": "Different", "expected_revision": 1}
    )
    with pytest.raises(ConflictError, match="idempotency key"):
        await service.upsert(changed)
