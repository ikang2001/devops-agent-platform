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
    TicketDraftStatus,
    TicketPriority,
    TicketSubmissionStatus,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.ticket_draft import TicketDraft
from devops_agent_platform.domain.models.ticket_submission import TicketSubmission
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyTicketSubmissionRepository,
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
from devops_agent_platform.infrastructure.database.mappers.ticket_submission import (
    TicketSubmissionMapper,
)
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建外部工单提交仓储测试的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(IncidentMapper.to_record(build_incident()))
        session.add(WorkflowRunMapper.to_record(build_workflow()))
        session.add(RCAReportMapper.to_record(build_report()))
        session.add(TicketDraftMapper.to_record(build_draft()))
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


def build_incident() -> Incident:
    """构造工单草稿依赖的事故。"""
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
    """构造已经成功结束的 RCA 工作流。"""
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
    """构造工单草稿依赖的 RCA 报告。"""
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
    """构造外部提交请求依赖的本地工单草稿。"""
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


def build_submission() -> TicketSubmission:
    """构造默认未完成的外部提交请求。"""
    return TicketSubmission(
        ticket_submission_id="tsb_001",
        tenant_id="tenant_001",
        ticket_draft_id="tdf_001",
        workflow_run_id="wfr_001",
        target_system="jira",
        status=TicketSubmissionStatus.REQUESTED,
        idempotency_key_hash="e" * 64,
        request_hash="f" * 64,
        requested_by="admin_001",
        trace_id="trc_submission_001",
        requested_at=NOW,
    )


async def test_repository_rejects_control_character_lookup_keys(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """仓储直连查询也必须拒绝污染租户、草稿、工作流和目标系统键。"""
    async with session_factory() as session:
        repository = SQLAlchemyTicketSubmissionRepository(session)

        with pytest.raises(AppValidationError, match="tenant_id"):
            await repository.get_by_id("tenant_001\nforged", "tsb_001")
        with pytest.raises(AppValidationError, match="ticket_submission_id"):
            await repository.get_by_id("tenant_001", "tsb_001\x7fforged")
        with pytest.raises(AppValidationError, match="ticket_draft_id"):
            await repository.get_by_draft_and_target(
                "tenant_001",
                "tdf_001\tforged",
                "jira",
            )
        with pytest.raises(AppValidationError, match="tenant_id"):
            await repository.get_by_draft_and_target(
                "tenant_001\x7fforged",
                "tdf_001",
                "jira",
            )
        with pytest.raises(AppValidationError, match="target_system"):
            await repository.get_by_draft_and_target(
                "tenant_001",
                "tdf_001",
                "jira\tinternal",
            )
        with pytest.raises(AppValidationError, match="target_system"):
            await repository.get_by_draft_and_target(
                "tenant_001",
                "tdf_001",
                "jira\x7finternal",
            )
        with pytest.raises(AppValidationError, match="workflow_run_id"):
            await repository.list_by_workflow(
                "tenant_001",
                "wfr_001\rforged",
            )
        with pytest.raises(AppValidationError, match="workflow_run_id"):
            await repository.list_by_workflow(
                "tenant_001",
                "wfr_001\x7fforged",
            )


async def test_stored_invalid_requested_submission_maps_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史脏提交请求行不能把领域异常泄漏给服务层。"""
    async with session_factory() as session:
        record = TicketSubmissionMapper.to_record(build_submission())
        record.trace_id = "trc_submission_001\nforged"
        session.add(record)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyTicketSubmissionRepository(session)
        for loader in (
            lambda: repository.get_by_id("tenant_001", "tsb_001"),
            lambda: repository.get_by_idempotency_key_hash(
                "tenant_001",
                "e" * 64,
            ),
            lambda: repository.get_by_draft_and_target(
                "tenant_001",
                "tdf_001",
                "jira",
            ),
            lambda: repository.list_by_workflow("tenant_001", "wfr_001"),
        ):
            with pytest.raises(
                PersistenceError,
                match="Stored ticket submission violates the domain contract",
            ):
                await loader()


async def test_stored_invalid_result_submission_maps_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史脏结果行在结果幂等回放路径也要稳定失败。"""
    submitted = build_submission().mark_submitted(
        external_ticket_id="JIRA-101",
        external_ticket_url="https://jira.example/browse/JIRA-101",
        completed_at=NOW + timedelta(minutes=1),
        idempotency_key_hash="1" * 64,
        request_hash="2" * 64,
        trace_id="trc_result_001",
        completed_by="ticket-worker-001",
    )
    async with session_factory() as session:
        record = TicketSubmissionMapper.to_record(submitted)
        record.external_ticket_id = "JIRA-101\nforged"
        session.add(record)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyTicketSubmissionRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored ticket submission violates the domain contract",
        ):
            await repository.get_by_result_idempotency_key_hash(
                "tenant_001",
                "1" * 64,
            )
