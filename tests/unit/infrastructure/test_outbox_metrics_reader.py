from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.domain.enums import OutboxStatus
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyOutboxMetricsReader,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建Outbox积压读取器测试使用的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def build_record(
    event_id: str,
    status: OutboxStatus,
    created_at: datetime,
) -> OutboxEventRecord:
    """构造满足表约束的Outbox数据库记录。"""
    return OutboxEventRecord(
        event_id=event_id,
        tenant_id="tenant_001",
        aggregate_type="Incident",
        aggregate_id="inc_001",
        event_type="incident.created",
        schema_version=1,
        payload={"incident_id": "inc_001"},
        occurred_at=created_at,
        trace_id="trc_001",
        status=status.value,
        attempts=0,
        available_at=created_at,
        created_at=created_at,
        published_at=(
            created_at if status is OutboxStatus.PUBLISHED else None
        ),
    )


async def test_reader_aggregates_backlog_with_one_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    records = [
        build_record(
            "evt_pending_old",
            OutboxStatus.PENDING,
            NOW - timedelta(minutes=10),
        ),
        build_record(
            "evt_pending_new",
            OutboxStatus.PENDING,
            NOW - timedelta(minutes=2),
        ),
        build_record(
            "evt_processing",
            OutboxStatus.PROCESSING,
            NOW - timedelta(minutes=5),
        ),
        build_record(
            "evt_failed",
            OutboxStatus.FAILED,
            NOW - timedelta(hours=2),
        ),
        build_record(
            "evt_published",
            OutboxStatus.PUBLISHED,
            NOW - timedelta(days=1),
        ),
    ]
    async with session_factory() as session:
        session.add_all(records)
        await session.commit()

    snapshot = await SQLAlchemyOutboxMetricsReader(
        session_factory
    ).load_snapshot()

    assert snapshot.count_for(OutboxStatus.PENDING) == 2
    assert snapshot.count_for(OutboxStatus.PROCESSING) == 1
    assert snapshot.count_for(OutboxStatus.FAILED) == 1
    assert snapshot.count_for(OutboxStatus.PUBLISHED) == 0
    assert snapshot.oldest_unpublished_at == NOW - timedelta(minutes=10)


async def test_reader_returns_zero_snapshot_for_empty_table(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    snapshot = await SQLAlchemyOutboxMetricsReader(
        session_factory
    ).load_snapshot()

    assert snapshot.oldest_unpublished_at is None
    assert all(count == 0 for _, count in snapshot.counts)
