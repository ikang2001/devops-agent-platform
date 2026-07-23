from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyOutboxRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建 Outbox 仓储测试使用的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def build_event(event_id: str = "evt_001") -> OutboxEvent:
    """构造有效的 IncidentCreated 事件。"""
    return OutboxEvent(
        event_id=event_id,
        tenant_id="tenant_001",
        aggregate_type="Incident",
        aggregate_id="inc_001",
        event_type="incident.created",
        schema_version=1,
        payload={"incident_id": "inc_001", "alert_id": "alt_001"},
        occurred_at=datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        trace_id="trc_001",
    )


async def test_add_persists_pending_event_after_caller_commit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = build_event()

    async with session_factory() as session:
        await SQLAlchemyOutboxRepository(session).add(event)
        await session.commit()

    async with session_factory() as session:
        record = await session.get(OutboxEventRecord, event.event_id)

    assert record is not None
    assert record.status == "PENDING"
    assert record.attempts == 0
    assert record.payload["alert_id"] == "alt_001"


async def test_duplicate_event_id_is_mapped_to_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = build_event()
    async with session_factory() as session:
        await SQLAlchemyOutboxRepository(session).add(event)
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(ConflictError):
            await SQLAlchemyOutboxRepository(session).add(event)
        await session.rollback()


def test_outbox_table_contains_dispatch_and_aggregate_indexes() -> None:
    index_names = {index.name for index in OutboxEventRecord.__table__.indexes}

    assert index_names == {
        "ix_outbox_aggregate",
        "ix_outbox_backlog_status_created",
        "ix_outbox_dispatch",
        "ix_outbox_recovery",
    }
    backlog_index = next(
        index
        for index in OutboxEventRecord.__table__.indexes
        if index.name == "ix_outbox_backlog_status_created"
    )
    assert str(backlog_index.dialect_options["postgresql"]["where"]) == (
        "status IN ('PENDING', 'PROCESSING', 'FAILED')"
    )
