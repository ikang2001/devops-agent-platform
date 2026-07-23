from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import AlertSeverity, IncidentStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyIncidentRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.incident import IncidentRecord


@pytest.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """使用文件数据库支持两个独立 Session 的并发版本测试。"""
    database_url = URL.create(
        drivername="sqlite+aiosqlite",
        database=str(tmp_path / "incident_repository.db"),
    )
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def build_incident(
    incident_id: str = "inc_001",
    tenant_id: str = "tenant_001",
    service_name: str = "checkout-api",
    status: IncidentStatus = IncidentStatus.OPEN,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Incident:
    """构造有效事故聚合。"""
    now = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)
    return Incident(
        incident_id=incident_id,
        tenant_id=tenant_id,
        service_name=service_name,
        severity=AlertSeverity.CRITICAL,
        status=status,
        title="Checkout error rate is above threshold",
        created_at=created_at or now,
        updated_at=updated_at or now,
    )


async def test_save_and_get_incident_with_utc_timestamp(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    incident = build_incident()

    async with session_factory() as session:
        await SQLAlchemyIncidentRepository(session).save(incident)
        await session.commit()

    async with session_factory() as session:
        loaded = await SQLAlchemyIncidentRepository(session).get_by_id(
            incident.incident_id,
            incident.tenant_id,
        )

    assert loaded is not None
    assert loaded.status is IncidentStatus.OPEN
    assert loaded.version == 1
    assert loaded.created_at.tzinfo is UTC


async def test_get_by_id_enforces_tenant_isolation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    incident = build_incident()

    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        await repository.save(incident)
        await session.commit()

    async with session_factory() as session:
        loaded = await SQLAlchemyIncidentRepository(session).get_by_id(
            incident.incident_id,
            "another_tenant",
        )

    assert loaded is None


async def test_stored_invalid_incident_is_mapped_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史脏事故行应作为持久化完整性问题暴露。"""
    incident = build_incident()
    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        await repository.save(incident)
        await session.commit()

    async with session_factory() as session:
        record = await session.get(IncidentRecord, incident.incident_id)
        assert record is not None
        record.title = "Checkout error\nforged"
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored incident violates the domain contract",
        ):
            await repository.get_by_id(
                incident.incident_id,
                incident.tenant_id,
            )
        with pytest.raises(
            PersistenceError,
            match="Stored incident violates the domain contract",
        ):
            await repository.list_page(
                incident.tenant_id,
                frozenset({IncidentStatus.OPEN}),
                before_updated_at=None,
                before_incident_id=None,
                limit=50,
            )


@pytest.mark.parametrize(
    ("incident_id", "tenant_id"),
    [
        ("", "tenant_001"),
        (" inc_001", "tenant_001"),
        ("inc_001", ""),
        ("inc_001", "tenant_001 "),
    ],
)
async def test_get_by_id_rejects_invalid_lookup_values(
    session_factory: async_sessionmaker[AsyncSession],
    incident_id: str,
    tenant_id: str,
) -> None:
    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        with pytest.raises(AppValidationError):
            await repository.get_by_id(incident_id, tenant_id)


async def test_stale_version_cannot_overwrite_newer_incident(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    incident = build_incident()
    async with session_factory() as session:
        await SQLAlchemyIncidentRepository(session).save(incident)
        await session.commit()

    async with session_factory() as first_session:
        async with session_factory() as second_session:
            first_repository = SQLAlchemyIncidentRepository(first_session)
            second_repository = SQLAlchemyIncidentRepository(second_session)
            first_copy = await first_repository.get_by_id(
                incident.incident_id,
                incident.tenant_id,
            )
            stale_copy = await second_repository.get_by_id(
                incident.incident_id,
                incident.tenant_id,
            )
            assert first_copy is not None
            assert stale_copy is not None

            first_copy.title = "First operator update"
            first_copy.updated_at += timedelta(seconds=1)
            await first_repository.save(first_copy)
            await first_session.commit()
            assert first_copy.version == 2

            stale_copy.title = "Stale operator update"
            stale_copy.updated_at += timedelta(seconds=2)
            with pytest.raises(ConflictError, match="version conflict"):
                await second_repository.save(stale_copy)
            await second_session.rollback()

    async with session_factory() as session:
        current = await SQLAlchemyIncidentRepository(session).get_by_id(
            incident.incident_id,
            incident.tenant_id,
        )

    assert current is not None
    assert current.title == "First operator update"
    assert current.version == 2


async def test_update_cannot_move_incident_to_another_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    incident = build_incident()
    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        await repository.save(incident)
        await session.commit()

    incident.tenant_id = "another_tenant"
    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        with pytest.raises(ConflictError, match="immutable fields changed"):
            await repository.save(incident)
        await session.rollback()

    async with session_factory() as session:
        current = await SQLAlchemyIncidentRepository(session).get_by_id(
            incident.incident_id,
            "tenant_001",
        )

    assert current is not None
    assert current.tenant_id == "tenant_001"


async def test_list_page_uses_stable_keyset_and_status_filter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """分页按更新时间和 ID 倒序，游标后续页不重复上一页记录。"""
    newest = datetime(2026, 6, 27, 11, 0, tzinfo=UTC)
    incidents = [
        build_incident(
            incident_id="inc_c",
            updated_at=newest,
            status=IncidentStatus.OPEN,
        ),
        build_incident(
            incident_id="inc_b",
            updated_at=newest,
            status=IncidentStatus.ANALYZING,
        ),
        build_incident(
            incident_id="inc_a",
            updated_at=newest - timedelta(minutes=1),
            status=IncidentStatus.OPEN,
        ),
        build_incident(
            incident_id="inc_resolved",
            updated_at=newest + timedelta(minutes=1),
            status=IncidentStatus.RESOLVED,
        ),
        build_incident(
            incident_id="inc_other_tenant",
            tenant_id="tenant_002",
            updated_at=newest + timedelta(minutes=2),
        ),
    ]
    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        for incident in incidents:
            await repository.save(incident)
        await session.commit()

    statuses = frozenset(
        {IncidentStatus.OPEN, IncidentStatus.ANALYZING}
    )
    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        first = await repository.list_page(
            "tenant_001",
            statuses,
            before_updated_at=None,
            before_incident_id=None,
            limit=2,
        )
        second = await repository.list_page(
            "tenant_001",
            statuses,
            before_updated_at=first[-1].updated_at,
            before_incident_id=first[-1].incident_id,
            limit=2,
        )

    assert [item.incident_id for item in first] == ["inc_c", "inc_b"]
    assert [item.incident_id for item in second] == ["inc_a"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": ""},
        {"statuses": frozenset()},
        {
            "before_updated_at": datetime(
                2026,
                6,
                27,
                10,
                0,
                tzinfo=UTC,
            )
        },
        {"before_incident_id": "inc_001"},
        {"limit": 0},
        {"limit": 102},
    ],
)
async def test_list_page_rejects_invalid_boundaries(
    session_factory: async_sessionmaker[AsyncSession],
    overrides: dict[str, object],
) -> None:
    """仓储独立校验游标字段配对和查询容量。"""
    values: dict[str, object] = {
        "tenant_id": "tenant_001",
        "statuses": frozenset({IncidentStatus.OPEN}),
        "before_updated_at": None,
        "before_incident_id": None,
        "limit": 50,
    }
    values.update(overrides)

    async with session_factory() as session:
        with pytest.raises(AppValidationError):
            await SQLAlchemyIncidentRepository(session).list_page(
                **values  # type: ignore[arg-type]
            )


async def test_find_candidates_filters_and_orders_bounded_results(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert_time = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)
    matching_a = build_incident(
        incident_id="inc_match_a",
        created_at=alert_time - timedelta(minutes=10),
        updated_at=alert_time - timedelta(minutes=1),
    )
    matching_b = build_incident(
        incident_id="inc_match_b",
        status=IncidentStatus.ANALYZING,
        created_at=alert_time - timedelta(minutes=8),
        updated_at=alert_time - timedelta(minutes=1),
    )
    excluded = [
        build_incident(
            incident_id="inc_wrong_tenant",
            tenant_id="tenant_002",
        ),
        build_incident(
            incident_id="inc_wrong_service",
            service_name="payments-api",
        ),
        build_incident(
            incident_id="inc_resolved",
            status=IncidentStatus.RESOLVED,
        ),
        build_incident(
            incident_id="inc_too_old",
            created_at=alert_time - timedelta(minutes=30),
            updated_at=alert_time - timedelta(minutes=16),
        ),
        build_incident(
            incident_id="inc_created_too_late",
            created_at=alert_time + timedelta(minutes=3),
            updated_at=alert_time + timedelta(minutes=3),
        ),
    ]

    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        for incident in [matching_a, matching_b, *excluded]:
            await repository.save(incident)
        await session.commit()

    async with session_factory() as session:
        candidates = await SQLAlchemyIncidentRepository(
            session
        ).find_candidates(
            tenant_id="tenant_001",
            service_name="checkout-api",
            statuses=frozenset(
                {IncidentStatus.OPEN, IncidentStatus.ANALYZING}
            ),
            created_before=alert_time + timedelta(minutes=2),
            updated_after=alert_time - timedelta(minutes=15),
            limit=10,
        )

    assert [item.incident_id for item in candidates] == [
        "inc_match_b",
        "inc_match_a",
    ]


async def test_find_candidates_applies_hard_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert_time = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)
    incidents = [
        build_incident(
            incident_id=f"inc_limit_{index}",
            created_at=alert_time - timedelta(minutes=5),
            updated_at=alert_time - timedelta(seconds=index),
        )
        for index in range(1, 4)
    ]
    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        for incident in incidents:
            await repository.save(incident)
        await session.commit()

    async with session_factory() as session:
        candidates = await SQLAlchemyIncidentRepository(
            session
        ).find_candidates(
            tenant_id="tenant_001",
            service_name="checkout-api",
            statuses=frozenset({IncidentStatus.OPEN}),
            created_before=alert_time + timedelta(minutes=2),
            updated_after=alert_time - timedelta(minutes=15),
            limit=2,
        )

    assert [item.incident_id for item in candidates] == [
        "inc_limit_1",
        "inc_limit_2",
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": ""},
        {"service_name": " checkout-api"},
        {"statuses": frozenset()},
        {"statuses": {"OPEN"}},
        {"statuses": frozenset({"OPEN"})},
        {"created_before": datetime(2026, 6, 27, 10, 2)},
        {"updated_after": datetime(2026, 6, 27, 9, 45)},
        {"updated_after": datetime(2026, 6, 27, 10, 3, tzinfo=UTC)},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
    ],
)
async def test_find_candidates_rejects_invalid_query_boundaries(
    session_factory: async_sessionmaker[AsyncSession],
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "tenant_id": "tenant_001",
        "service_name": "checkout-api",
        "statuses": frozenset({IncidentStatus.OPEN}),
        "created_before": datetime(2026, 6, 27, 10, 2, tzinfo=UTC),
        "updated_after": datetime(2026, 6, 27, 9, 45, tzinfo=UTC),
        "limit": 50,
    }
    values.update(overrides)

    async with session_factory() as session:
        repository = SQLAlchemyIncidentRepository(session)
        with pytest.raises(AppValidationError):
            await repository.find_candidates(**values)  # type: ignore[arg-type]


def test_incident_table_contains_cursor_pagination_indexes() -> None:
    index_names = {index.name for index in IncidentRecord.__table__.indexes}

    assert index_names == {
        "ix_incidents_candidate_lookup",
        "ix_incidents_tenant_service_created_id",
        "ix_incidents_tenant_status_updated_id",
        "uq_incidents_tenant_closure_idempotency",
        "uq_incidents_tenant_resolution_idempotency",
    }
