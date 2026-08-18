from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from devops_agent_platform.domain.models.workspace import WorkspaceConfig
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyWorkspaceRepository,
)
from devops_agent_platform.infrastructure.database.base import Base


@pytest.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'workspace.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def workspace(
    workspace_id: str = "shop", tenant_id: str = "tenant-a"
) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        name="MiniShop",
        prometheus_target="http://prometheus:9090",
        loki_target="http://loki:3100",
        tempo_target="http://tempo:3200",
        allowed_tools=("metrics.query@v1", "logs.query@v1"),
        updated_at=datetime(2026, 8, 18, tzinfo=UTC),
    )


async def test_workspace_repository_round_trips_json_and_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = workspace()
    async with session_factory() as session:
        repository = SQLAlchemyWorkspaceRepository(session)
        await repository.save(item)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkspaceRepository(session)
        assert await repository.get("tenant-a", "shop") == item
        assert await repository.get("tenant-b", "shop") is None
        assert await repository.list_for_tenant("tenant-a") == (item,)
        assert await repository.list_for_tenant("tenant-b") == ()
