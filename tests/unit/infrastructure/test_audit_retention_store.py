import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.domain.enums import (
    AlertSeverity,
    EvidenceType,
    IncidentStatus,
    RCAConclusionStatus,
    ToolInvocationStatus,
    ToolRiskLevel,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyAuditRetentionStore,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.evidence import (
    EvidenceMapper,
)
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)
from devops_agent_platform.infrastructure.database.mappers.rca_report import (
    RCAReportMapper,
)
from devops_agent_platform.infrastructure.database.mappers.tool_invocation import (
    ToolInvocationMapper,
)
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)
from devops_agent_platform.infrastructure.database.models.evidence import (
    EvidenceRecord,
)
from devops_agent_platform.infrastructure.database.models.rca_report import (
    RCAReportRecord,
)
from devops_agent_platform.infrastructure.database.models.tool_invocation import (
    ToolInvocationRecord,
)
from devops_agent_platform.infrastructure.database.models.workflow_run import (
    WorkflowRunRecord,
)

NOW = datetime(2026, 6, 30, 14, 0, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=30)


@pytest.fixture
async def database() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    """创建审计清理测试使用的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        await engine.dispose()


def build_incident() -> Incident:
    """构造所有测试工作流共享的事故父记录。"""
    return Incident(
        incident_id="inc_retention",
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.RESOLVED,
        title="Resolved checkout incident",
        created_at=NOW - timedelta(days=60),
        updated_at=NOW,
    )


def build_workflow(
    workflow_run_id: str,
    ended_at: datetime,
) -> WorkflowRun:
    """构造已结束且尚未清理审计数据的工作流。"""
    return WorkflowRun(
        workflow_run_id=workflow_run_id,
        tenant_id="tenant_001",
        incident_id="inc_retention",
        operator_id="operator_001",
        idempotency_key_hash=hashlib.sha256(
            workflow_run_id.encode()
        ).hexdigest(),
        request_hash=hashlib.sha256(
            f"request:{workflow_run_id}".encode()
        ).hexdigest(),
        trace_id=f"trc_{workflow_run_id}",
        status=WorkflowRunStatus.SUCCEEDED,
        created_at=ended_at - timedelta(hours=1),
        updated_at=ended_at,
        started_at=ended_at - timedelta(minutes=30),
        ended_at=ended_at,
        step_count=1,
        execution_attempts=1,
    )


def build_evidence(workflow_run_id: str) -> Evidence:
    """构造待清理 Evidence。"""
    content_json, content_sha256 = build_evidence_content({"status": "ok"})
    return Evidence(
        evidence_id=(f"e{workflow_run_id}").ljust(64, "0")[:64],
        tenant_id="tenant_001",
        incident_id="inc_retention",
        workflow_run_id=workflow_run_id,
        execution_attempt=1,
        step_id="collect.logs",
        tool_name="logs.query",
        tool_version="v1",
        evidence_type=EvidenceType.LOG,
        source="loki",
        summary="logs collected",
        content_json=content_json,
        content_sha256=content_sha256,
        confidence=1.0,
        collected_at=NOW - timedelta(days=31),
    )


def build_invocation(workflow_run_id: str) -> ToolInvocation:
    """构造待清理工具调用记录。"""
    return ToolInvocation(
        invocation_id=(f"i{workflow_run_id}").ljust(64, "0")[:64],
        tenant_id="tenant_001",
        incident_id="inc_retention",
        workflow_run_id=workflow_run_id,
        execution_attempt=1,
        step_id="collect.logs",
        operator_id="operator_001",
        trace_id=f"trc_{workflow_run_id}",
        tool_name="logs.query",
        tool_version="v1",
        risk_level=ToolRiskLevel.LOW,
        status=ToolInvocationStatus.SUCCEEDED,
        input_summary="payload_fields=10",
        input_sha256="c" * 64,
        output_summary="logs collected",
        output_sha256="d" * 64,
        latency_ms=10,
        error_code=None,
        started_at=NOW - timedelta(days=31),
        ended_at=NOW - timedelta(days=31),
    )


def build_report(workflow_run_id: str) -> RCAReport:
    """构造应长期保留的 RCA 报告快照。"""
    evidence_id = (f"e{workflow_run_id}").ljust(64, "0")[:64]
    return RCAReport(
        report_id=(f"r{workflow_run_id}").ljust(64, "0")[:64],
        tenant_id="tenant_001",
        incident_id="inc_retention",
        workflow_run_id=workflow_run_id,
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.UNDETERMINED,
        title="Root cause requires human review",
        summary="No verified root cause candidate was produced.",
        confidence=0.0,
        evidence_ids=(evidence_id,),
        evidence_type_counts=(("LOG", 1),),
        recommendations=("Review cited evidence.",),
        generator_name="deterministic-evidence-summary",
        generator_version="v1",
        generated_at=NOW - timedelta(days=31),
    )


async def seed_workflow_audit(
    factory: async_sessionmaker[AsyncSession],
    workflow_run_id: str,
    ended_at: datetime,
) -> None:
    """在同一事务中写入父工作流和两类审计子记录。"""
    async with factory() as session:
        session.add(WorkflowRunMapper.to_record(
            build_workflow(workflow_run_id, ended_at)
        ))
        session.add(EvidenceMapper.to_record(
            build_evidence(workflow_run_id)
        ))
        session.add(ToolInvocationMapper.to_record(
            build_invocation(workflow_run_id)
        ))
        session.add(RCAReportMapper.to_record(
            build_report(workflow_run_id)
        ))
        await session.commit()


async def seed_retention_dataset(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """写入两个过期任务和一个恰好位于边界的任务。"""
    async with factory() as session:
        session.add(IncidentMapper.to_record(build_incident()))
        await session.commit()
    await seed_workflow_audit(
        factory,
        "wfr_oldest",
        NOW - timedelta(days=40),
    )
    await seed_workflow_audit(
        factory,
        "wfr_old",
        NOW - timedelta(days=35),
    )
    await seed_workflow_audit(factory, "wfr_boundary", CUTOFF)


async def audit_counts(
    factory: async_sessionmaker[AsyncSession],
    workflow_run_id: str,
) -> tuple[int, int, datetime | None, int]:
    """读取两类子记录数量、清理水位和乐观锁版本。"""
    async with factory() as session:
        evidence_count = await session.scalar(
            select(func.count())
            .select_from(EvidenceRecord)
            .where(EvidenceRecord.workflow_run_id == workflow_run_id)
        )
        invocation_count = await session.scalar(
            select(func.count())
            .select_from(ToolInvocationRecord)
            .where(ToolInvocationRecord.workflow_run_id == workflow_run_id)
        )
        workflow = await session.get(WorkflowRunRecord, workflow_run_id)
    assert workflow is not None
    return (
        int(evidence_count or 0),
        int(invocation_count or 0),
        workflow.audit_purged_at,
        workflow.version,
    )


async def report_count(
    factory: async_sessionmaker[AsyncSession],
    workflow_run_id: str,
) -> int:
    """查询清理后仍应保留的报告数量。"""
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(RCAReportRecord)
            .where(RCAReportRecord.workflow_run_id == workflow_run_id)
        )
    return int(count or 0)


async def test_purge_uses_watermark_and_strict_cutoff_in_small_batches(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """连续小批次应按结束时间推进，边界任务不能被提前删除。"""
    _, factory = database
    await seed_retention_dataset(factory)
    store = SQLAlchemyAuditRetentionStore(factory)

    assert await store.purge_batch(
        cutoff=CUTOFF,
        purged_at=NOW,
        limit=1,
    ) == 1
    assert await audit_counts(factory, "wfr_oldest") == (0, 0, NOW, 2)
    assert await report_count(factory, "wfr_oldest") == 1
    assert (await audit_counts(factory, "wfr_old"))[:2] == (1, 1)

    assert await store.purge_batch(
        cutoff=CUTOFF,
        purged_at=NOW,
        limit=1,
    ) == 1
    assert await audit_counts(factory, "wfr_old") == (0, 0, NOW, 2)
    assert await store.purge_batch(
        cutoff=CUTOFF,
        purged_at=NOW,
        limit=1,
    ) == 0
    assert (await audit_counts(factory, "wfr_boundary"))[:3] == (
        1,
        1,
        None,
    )


async def test_failure_updating_watermark_rolls_back_child_deletes(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """父水位更新失败时，前面的两个 DELETE 必须整体回滚。"""
    engine, factory = database
    await seed_retention_dataset(factory)
    store = SQLAlchemyAuditRetentionStore(factory)

    def fail_watermark_update(
        conn,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ) -> None:
        del conn, cursor, parameters, context, executemany
        normalized = statement.strip().upper()
        if normalized.startswith("UPDATE WORKFLOW_RUNS"):
            raise RuntimeError("injected watermark failure")

    event.listen(
        engine.sync_engine,
        "before_cursor_execute",
        fail_watermark_update,
    )
    try:
        with pytest.raises(RuntimeError, match="watermark failure"):
            await store.purge_batch(
                cutoff=CUTOFF,
                purged_at=NOW,
                limit=1,
            )
    finally:
        event.remove(
            engine.sync_engine,
            "before_cursor_execute",
            fail_watermark_update,
        )

    assert (await audit_counts(factory, "wfr_oldest"))[:3] == (
        1,
        1,
        None,
    )


def test_candidate_query_uses_postgresql_skip_locked() -> None:
    """多实例部署时候选查询必须跳过其他清理事务已锁定的任务。"""
    statement = SQLAlchemyAuditRetentionStore.build_candidate_statement(
        cutoff=CUTOFF,
        limit=100,
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "AUDIT_PURGED_AT IS NULL" in sql
    assert "ENDED_AT <" in sql
