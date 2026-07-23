from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    EvidenceType,
    IncidentStatus,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyEvidenceRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)
from devops_agent_platform.infrastructure.database.models.evidence import (
    EvidenceRecord,
)

NOW = datetime(2026, 6, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建带父级事故和工作流记录的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(IncidentMapper.to_record(build_incident()))
            session.add(WorkflowRunMapper.to_record(build_workflow_run()))
            await session.commit()
        yield factory
    finally:
        await engine.dispose()


def build_incident() -> Incident:
    """构造 Evidence 外键依赖的事故记录。"""
    return Incident(
        incident_id="inc_001",
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.ANALYZING,
        title="Checkout API failure",
        created_at=NOW,
        updated_at=NOW,
    )


def build_workflow_run() -> WorkflowRun:
    """构造处于运行中的 RCA 工作流记录。"""
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="user_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_001",
        status=WorkflowRunStatus.RUNNING,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        ended_at=None,
        step_count=0,
        lease_owner="worker_001",
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        execution_attempts=1,
    )


def build_evidence(
    evidence_id: str = "ev_001",
    *,
    step_id: str = "logs.query",
    evidence_type: EvidenceType = EvidenceType.LOG,
    source: str = "loki",
    collected_at: datetime = NOW,
) -> Evidence:
    """构造仓储测试使用的有效 Evidence。"""
    content_json, content_sha256 = build_evidence_content(
        {
            "window_minutes": 15,
            "sample": "checkout-api timeout",
            "source": source,
        }
    )
    return Evidence(
        evidence_id=evidence_id,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id=step_id,
        tool_name=step_id,
        tool_version="v1",
        evidence_type=evidence_type,
        source=source,
        summary=f"{source} evidence collected",
        content_json=content_json,
        content_sha256=content_sha256,
        confidence=0.8,
        collected_at=collected_at,
    )


async def test_save_persists_evidence_after_caller_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    evidence = build_evidence()

    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        await repository.save(evidence)
        await session.commit()

    async with session_factory() as session:
        record = await session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.evidence_id == evidence.evidence_id
            )
        )

    assert record is not None
    assert record.tenant_id == evidence.tenant_id
    assert record.workflow_run_id == evidence.workflow_run_id
    assert record.content_sha256 == evidence.content_sha256


async def test_save_does_not_commit_outer_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    evidence = build_evidence()

    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        await repository.save(evidence)
        await session.rollback()

    async with session_factory() as session:
        record = await session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.evidence_id == evidence.evidence_id
            )
        )

    assert record is None


async def test_get_by_id_isolated_by_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    evidence = build_evidence()

    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        await repository.save(evidence)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        same_tenant = await repository.get_by_id("tenant_001", "ev_001")
        other_tenant = await repository.get_by_id("tenant_002", "ev_001")

    assert same_tenant is not None
    assert same_tenant.evidence_id == "ev_001"
    assert other_tenant is None


async def test_list_by_workflow_run_returns_ordered_limited_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = build_evidence("ev_001", step_id="metrics.query", source="prometheus")
    second = build_evidence(
        "ev_002",
        step_id="logs.query",
        source="loki",
        collected_at=NOW + timedelta(seconds=1),
    )

    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        await repository.save(second)
        await repository.save(first)
        await session.commit()

    async with session_factory() as session:
        loaded = await SQLAlchemyEvidenceRepository(session).list_by_workflow_run(
            "tenant_001",
            "wfr_001",
            limit=1,
        )

    assert [item.evidence_id for item in loaded] == ["ev_001"]


async def test_stored_invalid_public_fields_are_mapped_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史脏数据应表现为持久化问题，而不是调用方入参错误。"""
    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        await repository.save(build_evidence())
        await session.commit()

    async with session_factory() as session:
        record = await session.get(EvidenceRecord, "ev_001")
        assert record is not None
        record.summary = "safe summary\nforged"
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored evidence violates the domain contract",
        ):
            await repository.get_by_id("tenant_001", "ev_001")
        with pytest.raises(
            PersistenceError,
            match="Stored evidence violates the domain contract",
        ):
            await repository.list_by_workflow_run("tenant_001", "wfr_001")


async def test_duplicate_workflow_step_source_is_mapped_to_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = build_evidence("ev_001")
    duplicate = build_evidence("ev_002")

    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        await repository.save(first)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        with pytest.raises(ConflictError):
            await repository.save(duplicate)
        await session.rollback()


async def test_invalid_query_limit_is_rejected_before_database_io(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = SQLAlchemyEvidenceRepository(session)
        with pytest.raises(AppValidationError):
            await repository.list_by_workflow_run(
                "tenant_001",
                "wfr_001",
                limit=0,
            )

    assert not session.in_transaction()


def test_evidence_table_contains_tenant_query_indexes() -> None:
    index_names = {index.name for index in EvidenceRecord.__table__.indexes}

    assert index_names == {
        "ix_evidence_tenant_incident_collected",
        "ix_evidence_tenant_type_collected",
        "ix_evidence_tenant_workflow_step",
    }
