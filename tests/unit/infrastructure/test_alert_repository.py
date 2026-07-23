from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import AlertSeverity
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.infrastructure.adapters.sqlalchemy.alert_repository import (
    SQLAlchemyAlertRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.alert import AlertRecord


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """为每个测试创建隔离的内存数据库和异步 Session 工厂。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def build_alert(
    alert_id: str = "alt_001",
    tenant_id: str = "tenant_001",
    external_event_id: str = "evt_001",
    starts_at: datetime | None = None,
) -> Alert:
    """构造不依赖 ORM 的有效领域告警。"""
    return Alert(
        alert_id=alert_id,
        tenant_id=tenant_id,
        source="alertmanager",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        summary="Checkout error rate is above threshold",
        starts_at=starts_at or datetime(2026, 6, 27, 8, 0, tzinfo=UTC),
        fingerprint="fp_checkout_error_rate",
        external_event_id=external_event_id,
    )


async def test_save_persists_domain_alert_after_caller_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert = build_alert()

    async with session_factory() as session:
        repository = SQLAlchemyAlertRepository(session)
        await repository.save(alert)
        await session.commit()

    async with session_factory() as session:
        record = await session.scalar(
            select(AlertRecord).where(AlertRecord.alert_id == alert.alert_id)
        )

    assert record is not None
    assert record.tenant_id == alert.tenant_id
    assert record.severity == AlertSeverity.CRITICAL.value
    assert record.fingerprint == alert.fingerprint
    assert record.external_event_id == alert.external_event_id


async def test_save_does_not_commit_outer_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert = build_alert()

    async with session_factory() as session:
        repository = SQLAlchemyAlertRepository(session)
        await repository.save(alert)
        await session.rollback()

    async with session_factory() as session:
        record = await session.scalar(
            select(AlertRecord).where(AlertRecord.alert_id == alert.alert_id)
        )

    assert record is None


async def test_duplicate_primary_key_is_mapped_to_conflict_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert = build_alert()

    async with session_factory() as session:
        await SQLAlchemyAlertRepository(session).save(alert)
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(ConflictError) as exc_info:
            await SQLAlchemyAlertRepository(session).save(alert)
        await session.rollback()

    assert exc_info.value.code == "CONFLICT"
    assert exc_info.value.__cause__ is not None


async def test_naive_timestamp_is_rejected_before_database_io(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    naive_timestamp = datetime(2026, 6, 27, 8, 0)

    async with session_factory() as session:
        repository = SQLAlchemyAlertRepository(session)
        with pytest.raises(AppValidationError):
            await repository.save(build_alert(starts_at=naive_timestamp))

    assert not session.in_transaction()


async def test_get_by_external_event_id_enforces_full_idempotency_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert = build_alert()

    async with session_factory() as session:
        repository = SQLAlchemyAlertRepository(session)
        await repository.save(alert)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyAlertRepository(session)
        loaded = await repository.get_by_external_event_id(
            alert.tenant_id,
            alert.source,
            alert.external_event_id,
        )
        another_tenant = await repository.get_by_external_event_id(
            "another_tenant",
            alert.source,
            alert.external_event_id,
        )

    assert loaded is not None
    assert loaded.alert_id == alert.alert_id
    assert another_tenant is None


async def test_stored_invalid_alert_is_mapped_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史脏告警行应作为持久化完整性问题暴露。"""
    alert = build_alert()
    async with session_factory() as session:
        repository = SQLAlchemyAlertRepository(session)
        await repository.save(alert)
        await session.commit()

    async with session_factory() as session:
        record = await session.get(AlertRecord, alert.alert_id)
        assert record is not None
        record.summary = "Checkout error\nforged"
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyAlertRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored alert violates the domain contract",
        ):
            await repository.get_by_external_event_id(
                alert.tenant_id,
                alert.source,
                alert.external_event_id,
            )


async def test_database_constraint_closes_check_then_insert_race(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = build_alert(alert_id="alt_race_001")
    second = build_alert(alert_id="alt_race_002")

    async with session_factory() as first_session:
        async with session_factory() as second_session:
            first_repository = SQLAlchemyAlertRepository(first_session)
            second_repository = SQLAlchemyAlertRepository(second_session)

            assert (
                await first_repository.get_by_external_event_id(
                    first.tenant_id,
                    first.source,
                    first.external_event_id,
                )
                is None
            )
            assert (
                await second_repository.get_by_external_event_id(
                    second.tenant_id,
                    second.source,
                    second.external_event_id,
                )
                is None
            )

            await first_repository.save(first)
            await first_session.commit()

            with pytest.raises(ConflictError):
                await second_repository.save(second)
            await second_session.rollback()


async def test_same_fingerprint_with_new_event_id_is_valid_recurrence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = build_alert(
        alert_id="alt_recurrence_001",
        external_event_id="evt_recurrence_001",
    )
    recurrence = build_alert(
        alert_id="alt_recurrence_002",
        external_event_id="evt_recurrence_002",
    )

    async with session_factory() as session:
        repository = SQLAlchemyAlertRepository(session)
        await repository.save(first)
        await repository.save(recurrence)
        await session.commit()


async def test_same_external_event_id_is_isolated_between_tenants(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = build_alert(alert_id="alt_tenant_001")
    another_tenant = build_alert(
        alert_id="alt_tenant_002",
        tenant_id="tenant_002",
    )

    async with session_factory() as session:
        repository = SQLAlchemyAlertRepository(session)
        await repository.save(first)
        await repository.save(another_tenant)
        await session.commit()


def test_alert_table_contains_tenant_query_indexes() -> None:
    index_names = {index.name for index in AlertRecord.__table__.indexes}

    assert index_names == {
        "ix_alerts_tenant_fingerprint",
        "ix_alerts_tenant_incident",
        "ix_alerts_tenant_starts_at",
    }
