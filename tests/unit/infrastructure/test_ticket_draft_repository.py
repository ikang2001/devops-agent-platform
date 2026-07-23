from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    IncidentStatus,
    RCAConclusionStatus,
    TicketDecision,
    TicketDraftStatus,
    TicketPriority,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.ticket_draft import TicketDraft
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyTicketDraftRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)
from devops_agent_platform.infrastructure.database.mappers.rca_report import (
    RCAReportMapper,
)
from devops_agent_platform.infrastructure.database.mappers.ticket_draft import (
    TicketDraftMapper,
)
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)

NOW = datetime(2026, 7, 3, 13, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建 TicketDraft 仓储测试的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(IncidentMapper.to_record(build_incident()))
        session.add(WorkflowRunMapper.to_record(build_workflow()))
        session.add(RCAReportMapper.to_record(build_report()))
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


def build_incident() -> Incident:
    """构造草稿依赖的事故。"""
    return Incident(
        incident_id="inc_001",
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.ANALYZING,
        title="Checkout outage",
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(minutes=30),
    )


def build_workflow() -> WorkflowRun:
    """构造草稿依赖的成功 RCA 工作流。"""
    started_at = NOW - timedelta(minutes=30)
    ended_at = NOW - timedelta(minutes=20)
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="operator_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_workflow_001",
        status=WorkflowRunStatus.SUCCEEDED,
        created_at=NOW - timedelta(minutes=31),
        updated_at=ended_at,
        started_at=started_at,
        ended_at=ended_at,
        step_count=3,
        execution_attempts=1,
    )


def build_report() -> RCAReport:
    """构造草稿依赖的 RCA 报告。"""
    return RCAReport(
        report_id="rpt_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.CONFIRMED,
        title="Dependency timeout",
        summary="Checkout dependency timed out.",
        confidence=0.9,
        evidence_ids=("evd_001",),
        evidence_type_counts=(("LOG", 1),),
        recommendations=("Open external ticket.",),
        generator_name="deterministic",
        generator_version="v1",
        generated_at=NOW - timedelta(minutes=19),
    )


def build_draft() -> TicketDraft:
    """构造默认草稿。"""
    return TicketDraft(
        ticket_draft_id="tdf_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        report_id="rpt_001",
        status=TicketDraftStatus.DRAFT,
        priority=TicketPriority.P1,
        title="Checkout dependency timeout",
        description="Open a follow-up ticket.",
        evidence_ids=("evd_001",),
        recommendations=("Open external ticket.",),
        created_by="admin_001",
        idempotency_key_hash="c" * 64,
        request_hash="d" * 64,
        trace_id="trc_draft_001",
        created_at=NOW - timedelta(minutes=18),
    )


async def test_repository_rejects_control_character_lookup_keys(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """仓储直连查询必须拒绝污染租户和工作流键。"""
    async with session_factory() as session:
        repository = SQLAlchemyTicketDraftRepository(session)

        with pytest.raises(AppValidationError, match="tenant_id"):
            await repository.get_by_workflow_run(
                "tenant_001\nforged",
                "wfr_001",
            )
        with pytest.raises(AppValidationError, match="workflow_run_id"):
            await repository.get_by_workflow_run(
                "tenant_001",
                "wfr_001\tforged",
            )
        with pytest.raises(AppValidationError, match="workflow_run_id"):
            await repository.get_by_workflow_run(
                "tenant_001",
                "wfr_001\x7fforged",
            )
        with pytest.raises(AppValidationError, match="tenant_id"):
            await repository.get_by_idempotency_key_hash(
                "tenant_001\rforged",
                "c" * 64,
            )
        with pytest.raises(AppValidationError, match="tenant_id"):
            await repository.get_by_idempotency_key_hash(
                "tenant_001\x7fforged",
                "c" * 64,
            )


async def test_stored_invalid_draft_maps_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史脏草稿行不能把领域异常泄漏给应用服务。"""
    async with session_factory() as session:
        record = TicketDraftMapper.to_record(build_draft())
        record.title = "Checkout dependency timeout\nforged"
        session.add(record)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyTicketDraftRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored ticket draft violates the domain contract",
        ):
            await repository.get_by_workflow_run("tenant_001", "wfr_001")
        with pytest.raises(
            PersistenceError,
            match="Stored ticket draft violates the domain contract",
        ):
            await repository.get_by_idempotency_key_hash(
                "tenant_001",
                "c" * 64,
            )


async def test_stored_invalid_decided_draft_maps_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """审批幂等回放路径也必须隔离历史脏审批字段。"""
    decided = build_draft().decide(
        TicketDecision.APPROVE,
        decided_by="admin_002",
        reason=None,
        decided_at=NOW,
        idempotency_key_hash="e" * 64,
        request_hash="f" * 64,
        trace_id="trc_decision_001",
    )
    async with session_factory() as session:
        record = TicketDraftMapper.to_record(decided)
        record.decision_trace_id = "trc_decision_001\nforged"
        session.add(record)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyTicketDraftRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored ticket draft violates the domain contract",
        ):
            await repository.get_by_decision_idempotency_key_hash(
                "tenant_001",
                "e" * 64,
            )
