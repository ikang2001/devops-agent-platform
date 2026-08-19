import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

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
    RootCauseType,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.rca_report import RCAReport, RCAReportCandidate
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyRCAReportRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)
from devops_agent_platform.infrastructure.database.models.rca_report import (
    RCAReportRecord,
)

NOW = datetime(2026, 6, 30, 18, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建包含事故和终态工作流的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(IncidentMapper.to_record(build_incident()))
            session.add(WorkflowRunMapper.to_record(build_workflow()))
            await session.commit()
        yield factory
    finally:
        await engine.dispose()


def build_incident() -> Incident:
    """构造报告所属事故。"""
    return Incident(
        incident_id="inc_001",
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.ANALYZING,
        title="Checkout failure",
        created_at=NOW,
        updated_at=NOW,
    )


def build_workflow() -> WorkflowRun:
    """构造报告所属成功工作流。"""
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="operator_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_001",
        status=WorkflowRunStatus.SUCCEEDED,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        ended_at=NOW,
        step_count=1,
        execution_attempts=1,
    )


def build_report(report_id: str = "c" * 64) -> RCAReport:
    """构造仓储测试使用的基础报告。"""
    return RCAReport(
        report_id=report_id,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.UNDETERMINED,
        title="Root cause requires human review",
        summary="Collected one LOG evidence item.",
        confidence=0.0,
        evidence_ids=("d" * 64,),
        evidence_type_counts=(("LOG", 1),),
        recommendations=("Review cited evidence.",),
        generator_name="deterministic-evidence-summary",
        generator_version="v1",
        generated_at=NOW,
    )


async def test_repository_persists_and_restores_structured_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """规范 JSON 字段应无损恢复为不可变领域结构。"""
    report = build_report()
    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        await repository.save(report)
        await session.commit()

    async with session_factory() as session:
        restored = await SQLAlchemyRCAReportRepository(
            session
        ).get_by_workflow_run("tenant_001", "wfr_001")

    assert restored == report
    assert restored is not None
    assert restored.evidence_type_counts == (("LOG", 1),)


async def test_repository_round_trips_root_cause_candidate_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    candidate = RCAReportCandidate(
        candidate_id="cand-inventory-timeout",
        service="inventory-service",
        root_type=RootCauseType.DEPENDENCY_TIMEOUT,
        resource="postgres",
        score=0.9,
        supporting_evidence_ids=("d" * 64,),
        source_evidence_types=("LOG", "TRACE"),
    )
    report = replace(
        build_report(),
        conclusion_status=RCAConclusionStatus.CANDIDATE,
        confidence=0.9,
        suspected_root_node="inventory-service",
        root_cause_type=RootCauseType.DEPENDENCY_TIMEOUT,
        root_cause_resource="postgres",
        selected_candidate_id=candidate.candidate_id,
        root_cause_candidates=(candidate,),
    )
    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        await repository.save(report)
        await session.commit()

    async with session_factory() as session:
        restored = await SQLAlchemyRCAReportRepository(
            session
        ).get_by_workflow_run("tenant_001", "wfr_001")

    assert restored == report
    assert restored is not None
    assert restored.root_cause_candidates == (candidate,)


async def test_repository_does_not_commit_outer_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """仓储只 flush，回滚权属于 UnitOfWork。"""
    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        await repository.save(build_report())
        await session.rollback()

    async with session_factory() as session:
        restored = await SQLAlchemyRCAReportRepository(
            session
        ).get_by_workflow_run("tenant_001", "wfr_001")
    assert restored is None


async def test_repository_enforces_one_report_per_execution_attempt(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一工作流执行代次不能生成两个相互冲突的报告。"""
    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        await repository.save(build_report())
        with pytest.raises(ConflictError):
            await repository.save(build_report("e" * 64))
        await session.rollback()


async def test_repository_hides_cross_tenant_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """查询必须携带租户边界。"""
    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        await repository.save(build_report())
        await session.commit()

    async with session_factory() as session:
        restored = await SQLAlchemyRCAReportRepository(
            session
        ).get_by_workflow_run("tenant_other", "wfr_001")
    assert restored is None


async def test_repository_revalidates_report_before_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """即使调用方绕过 frozen 对象，Mapper 也不能写入污染报告。"""
    report = build_report()
    object.__setattr__(report, "title", "Root cause\x7fforged")

    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        with pytest.raises(AppValidationError, match="control characters"):
            await repository.save(report)
        await session.rollback()

    async with session_factory() as session:
        assert await session.get(RCAReportRecord, "c" * 64) is None


async def test_repository_escapes_legacy_display_text_on_read(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史展示文本可兼容读取，且换行与完整转义均可审计。"""
    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        await repository.save(build_report())
        await session.commit()

    async with session_factory() as session:
        record = await session.get(RCAReportRecord, "c" * 64)
        assert record is not None
        record.title = "Root\tcause\x7freview"
        record.summary = "Evidence collected.\nReview\towner\x7fnow."
        record.recommendations_json = json.dumps(
            [
                "Review\tlogs\x7fnow.",
                f"{'x' * 1021}\x7f",
            ],
        )
        await session.commit()

    async with session_factory() as session:
        restored = await SQLAlchemyRCAReportRepository(
            session
        ).get_by_workflow_run("tenant_001", "wfr_001")

    assert restored is not None
    assert restored.title == "Root\\u0009cause\\u007freview"
    assert restored.summary == (
        "Evidence collected.\nReview\\u0009owner\\u007fnow."
    )
    assert restored.recommendations == (
        "Review\\u0009logs\\u007fnow.",
        "x" * 1021,
    )


async def test_repository_rejects_legacy_identity_contamination(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """生成器版本属于审计身份，历史污染不能通过转义伪装成新身份。"""
    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        await repository.save(build_report())
        await session.commit()

    async with session_factory() as session:
        record = await session.get(RCAReportRecord, "c" * 64)
        assert record is not None
        record.generator_version = "v1\x7fforged"
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored RCA report violates the domain contract",
        ):
            await repository.get_by_workflow_run("tenant_001", "wfr_001")


async def test_repository_rejects_control_contaminated_query_keys(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """租户和工作流查询键必须在发出 SQL 前拒绝控制字符。"""
    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        with pytest.raises(AppValidationError, match="tenant_id"):
            await repository.get_by_workflow_run(
                "tenant_001\x7fforged",
                "wfr_001",
            )
        with pytest.raises(AppValidationError, match="workflow_run_id"):
            await repository.get_by_workflow_run(
                "tenant_001",
                "wfr_001\tforged",
            )


async def test_stored_invalid_json_is_mapped_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史坏报告结构应作为持久化完整性问题暴露。"""
    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        await repository.save(build_report())
        await session.commit()

    async with session_factory() as session:
        record = await session.get(RCAReportRecord, "c" * 64)
        assert record is not None
        record.recommendations_json = "not-json"
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyRCAReportRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored RCA report violates the domain contract",
        ):
            await repository.get_by_workflow_run("tenant_001", "wfr_001")
