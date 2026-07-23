from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyOutboxDispatchStore,
    SQLAlchemyOutboxRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)

NOW = datetime(2030, 6, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建发布状态测试使用的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def build_event(event_id: str = "evt_dispatch_001") -> OutboxEvent:
    """构造待发布事件。"""
    return OutboxEvent(
        event_id=event_id,
        tenant_id="tenant_001",
        aggregate_type="Incident",
        aggregate_id="inc_001",
        event_type="incident.created",
        schema_version=1,
        payload={"incident_id": "inc_001"},
        occurred_at=NOW - timedelta(minutes=1),
        trace_id="trc_001",
    )


async def insert_event(
    session_factory: async_sessionmaker[AsyncSession],
    event_id: str = "evt_dispatch_001",
) -> None:
    """通过写入仓储创建一条PENDING事件。"""
    async with session_factory() as session:
        await SQLAlchemyOutboxRepository(session).add(build_event(event_id))
        await session.commit()


async def load_record(
    session_factory: async_sessionmaker[AsyncSession],
    event_id: str = "evt_dispatch_001",
) -> OutboxEventRecord:
    """使用独立Session读取状态，避免命中旧身份映射缓存。"""
    async with session_factory() as session:
        record = await session.get(OutboxEventRecord, event_id)
    assert record is not None
    return record


async def test_claim_batch_creates_committed_worker_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await insert_event(session_factory)
    store = SQLAlchemyOutboxDispatchStore(session_factory)

    claimed = await store.claim_batch(
        worker_id="worker_001",
        now=NOW,
        locked_until=NOW + timedelta(minutes=2),
        limit=10,
        max_attempts=5,
    )

    assert len(claimed) == 1
    assert claimed[0].event.event_id == "evt_dispatch_001"
    assert claimed[0].attempts == 1
    record = await load_record(session_factory)
    assert record.status == "PROCESSING"
    assert record.attempts == 1
    assert record.lock_id == "worker_001"


async def test_mark_published_requires_current_lease_owner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await insert_event(session_factory)
    store = SQLAlchemyOutboxDispatchStore(session_factory)
    await store.claim_batch(
        "worker_001",
        NOW,
        NOW + timedelta(minutes=2),
        10,
        5,
    )

    with pytest.raises(ConflictError, match="ownership lost"):
        await store.mark_published(
            "evt_dispatch_001",
            "worker_002",
            NOW,
        )

    await store.mark_published("evt_dispatch_001", "worker_001", NOW)
    record = await load_record(session_factory)
    assert record.status == "PUBLISHED"
    assert record.published_at == NOW
    assert record.lock_id is None


@pytest.mark.parametrize(
    "operation",
    ["claim", "publish", "retry", "fail"],
)
async def test_dispatch_store_rejects_newline_worker_id(
    session_factory: async_sessionmaker[AsyncSession],
    operation: str,
) -> None:
    """Outbox租约写入也复用统一worker身份规则。"""
    await insert_event(session_factory)
    store = SQLAlchemyOutboxDispatchStore(session_factory)
    if operation != "claim":
        await store.claim_batch(
            "worker_001",
            NOW,
            NOW + timedelta(minutes=2),
            10,
            5,
        )

    with pytest.raises(
        AppValidationError,
        match="worker_id",
    ):
        if operation == "claim":
            await store.claim_batch(
                "worker\n001",
                NOW,
                NOW + timedelta(minutes=2),
                10,
                5,
            )
        elif operation == "publish":
            await store.mark_published(
                "evt_dispatch_001",
                "worker\n001",
                NOW,
            )
        elif operation == "retry":
            await store.mark_retry(
                "evt_dispatch_001",
                "worker\n001",
                NOW + timedelta(seconds=30),
                "TimeoutError",
            )
        else:
            await store.mark_failed(
                "evt_dispatch_001",
                "worker\n001",
                "TimeoutError",
            )

    record = await load_record(session_factory)
    if operation == "claim":
        assert record.status == "PENDING"
        assert record.attempts == 0
        assert record.lock_id is None
    else:
        assert record.status == "PROCESSING"
        assert record.attempts == 1
        assert record.lock_id == "worker_001"


@pytest.mark.parametrize(
    "operation",
    ["publish", "retry", "fail"],
)
async def test_dispatch_store_rejects_control_character_event_id(
    session_factory: async_sessionmaker[AsyncSession],
    operation: str,
) -> None:
    """状态更新入口不能接受会污染日志和索引的事件 ID。"""
    await insert_event(session_factory)
    store = SQLAlchemyOutboxDispatchStore(session_factory)
    await store.claim_batch(
        "worker_001",
        NOW,
        NOW + timedelta(minutes=2),
        10,
        5,
    )

    with pytest.raises(AppValidationError, match="control characters"):
        if operation == "publish":
            await store.mark_published(
                "evt_dispatch_001\nforged",
                "worker_001",
                NOW,
            )
        elif operation == "retry":
            await store.mark_retry(
                "evt_dispatch_001\nforged",
                "worker_001",
                NOW + timedelta(seconds=30),
                "TimeoutError",
            )
        else:
            await store.mark_failed(
                "evt_dispatch_001\nforged",
                "worker_001",
                "TimeoutError",
            )

    record = await load_record(session_factory)
    assert record.status == "PROCESSING"
    assert record.lock_id == "worker_001"


async def test_retry_and_failed_reject_control_character_error_summary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """失败摘要必须保持单行，避免污染排障表和运维导出。"""
    await insert_event(session_factory)
    store = SQLAlchemyOutboxDispatchStore(session_factory)
    await store.claim_batch(
        "worker_001",
        NOW,
        NOW + timedelta(minutes=2),
        10,
        5,
    )

    with pytest.raises(AppValidationError, match="last_error"):
        await store.mark_retry(
            "evt_dispatch_001",
            "worker_001",
            NOW + timedelta(seconds=30),
            "TimeoutError\tforged",
        )
    with pytest.raises(AppValidationError, match="last_error"):
        await store.mark_failed(
            "evt_dispatch_001",
            "worker_001",
            "TimeoutError\nforged",
        )
    with pytest.raises(AppValidationError, match="last_error"):
        await store.mark_failed(
            "evt_dispatch_001",
            "worker_001",
            "TimeoutError\x7fforged",
        )

    record = await load_record(session_factory)
    assert record.status == "PROCESSING"
    assert record.last_error is None


async def test_historical_dirty_event_is_not_claimed_for_publication(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史脏 Outbox 行应隔离为持久化错误，而不是进入发布器。"""
    await insert_event(session_factory)
    async with session_factory() as session:
        record = await session.get(OutboxEventRecord, "evt_dispatch_001")
        assert record is not None
        record.trace_id = "trc_001\nforged"
        await session.commit()

    store = SQLAlchemyOutboxDispatchStore(session_factory)
    with pytest.raises(
        PersistenceError,
        match="Stored outbox event violates the event contract",
    ):
        await store.claim_batch(
            "worker_001",
            NOW,
            NOW + timedelta(minutes=2),
            10,
            5,
        )

    record = await load_record(session_factory)
    assert record.status == "PENDING"
    assert record.attempts == 0
    assert record.lock_id is None


async def test_retry_releases_lease_and_respects_available_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await insert_event(session_factory)
    store = SQLAlchemyOutboxDispatchStore(session_factory)
    await store.claim_batch(
        "worker_001",
        NOW,
        NOW + timedelta(minutes=2),
        10,
        5,
    )
    retry_at = NOW + timedelta(seconds=30)

    await store.mark_retry(
        "evt_dispatch_001",
        "worker_001",
        retry_at,
        "TimeoutError",
    )

    assert (
        await store.claim_batch(
            "worker_002",
            NOW + timedelta(seconds=29),
            NOW + timedelta(minutes=2),
            10,
            5,
        )
        == []
    )
    reclaimed = await store.claim_batch(
        "worker_002",
        retry_at,
        retry_at + timedelta(minutes=2),
        10,
        5,
    )
    assert reclaimed[0].attempts == 2


async def test_expired_lease_is_reclaimed_by_another_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await insert_event(session_factory)
    store = SQLAlchemyOutboxDispatchStore(session_factory)
    await store.claim_batch(
        "worker_001",
        NOW,
        NOW + timedelta(seconds=10),
        10,
        5,
    )

    reclaimed = await store.claim_batch(
        "worker_002",
        NOW + timedelta(seconds=11),
        NOW + timedelta(minutes=2),
        10,
        5,
    )

    assert reclaimed[0].attempts == 2
    record = await load_record(session_factory)
    assert record.lock_id == "worker_002"


async def test_exhausted_expired_lease_is_marked_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await insert_event(session_factory)
    async with session_factory() as session:
        record = await session.get(OutboxEventRecord, "evt_dispatch_001")
        assert record is not None
        record.status = "PROCESSING"
        record.attempts = 5
        record.lock_id = "dead_worker"
        record.locked_until = NOW - timedelta(seconds=1)
        await session.commit()

    store = SQLAlchemyOutboxDispatchStore(session_factory)
    claimed = await store.claim_batch(
        "worker_002",
        NOW,
        NOW + timedelta(minutes=2),
        10,
        5,
    )

    assert claimed == []
    record = await load_record(session_factory)
    assert record.status == "FAILED"
    assert record.lock_id is None
    assert "maximum attempts" in (record.last_error or "")


def test_claim_statement_uses_postgresql_skip_locked() -> None:
    statement = SQLAlchemyOutboxDispatchStore._build_claim_statement(
        now=NOW,
        limit=20,
        max_attempts=5,
    )

    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT 20" in sql
