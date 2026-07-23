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
    IncidentStatus,
    ToolInvocationStatus,
    ToolRiskLevel,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyToolInvocationRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)
from devops_agent_platform.infrastructure.database.models.tool_invocation import (
    ToolInvocationRecord,
)

NOW = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建包含事故与工作流父记录的隔离数据库。"""
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
    """构造审计记录引用的事故。"""
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
    """构造审计记录引用的运行中工作流。"""
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="operator_001",
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


def build_invocation(
    invocation_id: str = "c" * 64,
    *,
    step_id: str = "collect.logs",
    started_at: datetime = NOW,
) -> ToolInvocation:
    """构造仓储测试使用的成功调用记录。"""
    return ToolInvocation(
        invocation_id=invocation_id,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id=step_id,
        operator_id="operator_001",
        trace_id="trc_001",
        tool_name="logs.query",
        tool_version="v1",
        risk_level=ToolRiskLevel.LOW,
        status=ToolInvocationStatus.SUCCEEDED,
        input_summary="payload_fields=10",
        input_sha256="d" * 64,
        output_summary="logs evidence collected",
        output_sha256="e" * 64,
        latency_ms=20,
        error_code=None,
        started_at=started_at,
        ended_at=started_at + timedelta(milliseconds=20),
    )


async def test_save_requires_caller_commit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """仓储只 flush，提交和回滚必须由事务边界决定。"""
    invocation = build_invocation()
    async with session_factory() as session:
        repository = SQLAlchemyToolInvocationRepository(session)
        await repository.save(invocation)
        await session.rollback()

    async with session_factory() as session:
        record = await session.scalar(
            select(ToolInvocationRecord).where(
                ToolInvocationRecord.invocation_id
                == invocation.invocation_id
            )
        )
    assert record is None


async def test_save_and_list_are_tenant_scoped_and_ordered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """查询必须带租户，并按开始时间稳定排序。"""
    later = build_invocation(
        "f" * 64,
        step_id="collect.traces",
        started_at=NOW + timedelta(seconds=1),
    )
    earlier = build_invocation()
    async with session_factory() as session:
        repository = SQLAlchemyToolInvocationRepository(session)
        await repository.save(later)
        await repository.save(earlier)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyToolInvocationRepository(session)
        records = await repository.list_by_workflow_run(
            "tenant_001",
            "wfr_001",
        )
        foreign_tenant = await repository.list_by_workflow_run(
            "tenant_other",
            "wfr_001",
        )

    assert [item.invocation_id for item in records] == [
        earlier.invocation_id,
        later.invocation_id,
    ]
    assert foreign_tenant == []


async def test_duplicate_workflow_step_is_a_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一执行代次的同一步骤不能被重复写入。"""
    async with session_factory() as session:
        repository = SQLAlchemyToolInvocationRepository(session)
        await repository.save(build_invocation())
        with pytest.raises(ConflictError):
            await repository.save(build_invocation("f" * 64))
        await session.rollback()


async def test_stored_invalid_public_fields_are_mapped_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史脏审计行应表现为持久化问题，便于定位数据完整性风险。"""
    async with session_factory() as session:
        repository = SQLAlchemyToolInvocationRepository(session)
        await repository.save(build_invocation())
        await session.commit()

    async with session_factory() as session:
        record = await session.get(ToolInvocationRecord, "c" * 64)
        assert record is not None
        record.output_summary = "logs evidence\nforged"
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyToolInvocationRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored tool invocation violates the domain contract",
        ):
            await repository.list_by_workflow_run("tenant_001", "wfr_001")


@pytest.mark.parametrize("limit", [0, 501, True])
async def test_list_rejects_unbounded_limit(
    session_factory: async_sessionmaker[AsyncSession],
    limit: object,
) -> None:
    """仓储层也要限制读取量，不能只依赖接口层校验。"""
    async with session_factory() as session:
        repository = SQLAlchemyToolInvocationRepository(session)
        with pytest.raises(AppValidationError, match="limit"):
            await repository.list_by_workflow_run(
                "tenant_001",
                "wfr_001",
                limit=limit,  # type: ignore[arg-type]
            )


def test_model_declares_audit_query_indexes() -> None:
    """数据库模型应包含工作流、事故和状态查询索引。"""
    index_names = {
        index.name for index in ToolInvocationRecord.__table__.indexes
    }
    assert "ix_tool_invocation_tenant_workflow_started" in index_names
    assert "ix_tool_invocation_tenant_incident_started" in index_names
    assert "ix_tool_invocation_tenant_status_started" in index_names
