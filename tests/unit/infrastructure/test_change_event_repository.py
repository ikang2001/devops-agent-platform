from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.domain.enums import ChangeEventStatus, ChangeType
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.change_event import (
    ChangeEvent,
    build_change_metadata,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy.change_event_repository import (  # noqa: E501
    SQLAlchemyChangeEventRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.change_event import (
    ChangeEventRecord,
)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def build_change_event(
    change_event_id: str = "chg_001",
    *,
    tenant_id: str = "tenant_001",
    service_name: str = "payment-service",
    external_event_id: str = "deploy_payment_v2",
    started_at: datetime | None = None,
) -> ChangeEvent:
    start = started_at or datetime(2026, 8, 17, 14, 2, tzinfo=UTC)
    return ChangeEvent(
        change_event_id=change_event_id,
        tenant_id=tenant_id,
        source="argocd",
        external_event_id=external_event_id,
        service_name=service_name,
        resource_type="deployment",
        resource_id="payment-service",
        change_type=ChangeType.DEPLOYMENT,
        status=ChangeEventStatus.SUCCEEDED,
        version_before="v1",
        version_after="v2",
        operator_id="deployment-bot",
        summary="payment-service upgraded from v1 to v2",
        metadata_json=build_change_metadata({"cluster": "minishop"}),
        started_at=start,
        completed_at=start + timedelta(minutes=1),
        created_at=start + timedelta(minutes=2),
        request_hash="a" * 64,
    )


async def test_save_persists_change_event_only_after_caller_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = build_change_event()

    async with session_factory() as session:
        repository = SQLAlchemyChangeEventRepository(session)
        await repository.save(event)
        await session.commit()

    async with session_factory() as session:
        record = await session.scalar(
            select(ChangeEventRecord).where(
                ChangeEventRecord.change_event_id == event.change_event_id
            )
        )

    assert record is not None
    assert record.tenant_id == event.tenant_id
    assert record.change_type == ChangeType.DEPLOYMENT.value
    assert record.metadata_json == '{"cluster":"minishop"}'


async def test_save_does_not_commit_outer_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = build_change_event()

    async with session_factory() as session:
        await SQLAlchemyChangeEventRepository(session).save(event)
        await session.rollback()

    async with session_factory() as session:
        assert await session.get(ChangeEventRecord, event.change_event_id) is None


async def test_unique_idempotency_key_closes_check_then_insert_race(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = build_change_event("chg_first")
    duplicate = build_change_event("chg_duplicate")

    async with session_factory() as session:
        repository = SQLAlchemyChangeEventRepository(session)
        await repository.save(first)
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(ConflictError):
            await SQLAlchemyChangeEventRepository(session).save(duplicate)
        await session.rollback()


async def test_get_queries_enforce_tenant_and_full_idempotency_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = build_change_event()
    async with session_factory() as session:
        repository = SQLAlchemyChangeEventRepository(session)
        await repository.save(event)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyChangeEventRepository(session)
        by_id = await repository.get_by_id(event.change_event_id, event.tenant_id)
        wrong_tenant = await repository.get_by_id(
            event.change_event_id,
            "tenant_002",
        )
        by_external_id = await repository.get_by_external_event_id(
            event.tenant_id,
            event.source,
            event.external_event_id,
        )

    assert by_id == event
    assert wrong_tenant is None
    assert by_external_id == event


async def test_list_in_time_window_is_bounded_ordered_and_tenant_isolated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    anchor = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)
    events = [
        build_change_event(
            "chg_before",
            external_event_id="evt_before",
            started_at=anchor - timedelta(seconds=1),
        ),
        build_change_event(
            "chg_start",
            external_event_id="evt_start",
            started_at=anchor,
        ),
        build_change_event(
            "chg_middle",
            external_event_id="evt_middle",
            started_at=anchor + timedelta(minutes=5),
        ),
        build_change_event(
            "chg_end",
            external_event_id="evt_end",
            started_at=anchor + timedelta(minutes=10),
        ),
        build_change_event(
            "chg_after",
            external_event_id="evt_after",
            started_at=anchor + timedelta(minutes=10, seconds=1),
        ),
        build_change_event(
            "chg_other_tenant",
            tenant_id="tenant_002",
            external_event_id="evt_other_tenant",
            started_at=anchor + timedelta(minutes=7),
        ),
        build_change_event(
            "chg_other_service",
            service_name="checkout-api",
            external_event_id="evt_other_service",
            started_at=anchor + timedelta(minutes=8),
        ),
    ]
    async with session_factory() as session:
        repository = SQLAlchemyChangeEventRepository(session)
        for event in events:
            await repository.save(event)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyChangeEventRepository(session)
        loaded = await repository.list_in_time_window(
            "tenant_001",
            "payment-service",
            anchor,
            anchor + timedelta(minutes=10),
            limit=2,
        )

    assert [event.change_event_id for event in loaded] == [
        "chg_end",
        "chg_middle",
    ]


async def test_list_for_service_returns_latest_bounded_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    anchor = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)
    async with session_factory() as session:
        repository = SQLAlchemyChangeEventRepository(session)
        await repository.save(
            build_change_event(
                "chg_old",
                external_event_id="evt_old",
                started_at=anchor,
            )
        )
        await repository.save(
            build_change_event(
                "chg_new",
                external_event_id="evt_new",
                started_at=anchor + timedelta(minutes=1),
            )
        )
        await session.commit()

    async with session_factory() as session:
        loaded = await SQLAlchemyChangeEventRepository(session).list_for_service(
            "tenant_001",
            "payment-service",
            limit=1,
        )

    assert [event.change_event_id for event in loaded] == ["chg_new"]


@pytest.mark.parametrize("limit", [0, 101, True])
async def test_list_queries_reject_unbounded_limit(
    session_factory: async_sessionmaker[AsyncSession],
    limit: int,
) -> None:
    async with session_factory() as session:
        repository = SQLAlchemyChangeEventRepository(session)
        with pytest.raises(AppValidationError, match="limit"):
            await repository.list_for_service(
                "tenant_001",
                "payment-service",
                limit=limit,
            )


async def test_time_window_rejects_naive_or_reversed_boundaries(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    aware = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)
    naive = datetime(2026, 8, 17, 14, 0)

    async with session_factory() as session:
        repository = SQLAlchemyChangeEventRepository(session)
        with pytest.raises(AppValidationError, match="timezone"):
            await repository.list_in_time_window(
                "tenant_001",
                "payment-service",
                naive,
                aware,
                limit=10,
            )
        with pytest.raises(AppValidationError, match="must not precede"):
            await repository.list_in_time_window(
                "tenant_001",
                "payment-service",
                aware + timedelta(minutes=1),
                aware,
                limit=10,
            )


def test_change_event_table_contains_idempotency_and_window_indexes() -> None:
    unique_constraints = {
        constraint.name
        for constraint in ChangeEventRecord.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    index_names = {index.name for index in ChangeEventRecord.__table__.indexes}

    assert "uq_change_events_tenant_source_external_event" in unique_constraints
    assert index_names == {"ix_change_events_tenant_service_started"}
