from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.workflow_execution import (
    ClaimWorkflowRunCommand,
    CompleteWorkflowRunCommand,
    HeartbeatWorkflowRunCommand,
)
from devops_agent_platform.application.exceptions import WorkflowLeaseLostError
from devops_agent_platform.application.services.workflow_execution_service import (
    WorkflowClaimConfig,
    WorkflowExecutionApplicationService,
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
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
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
    SQLAlchemyEvidenceRepository,
    SQLAlchemyIncidentRepository,
    SQLAlchemyRCAReportRepository,
    SQLAlchemyToolInvocationRepository,
    SQLAlchemyUnitOfWork,
    SQLAlchemyWorkflowRunRepository,
)
from devops_agent_platform.infrastructure.database.base import Base

NOW = datetime(2026, 6, 28, 14, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建工作流执行应用服务使用的真实异步事务环境。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


def build_command(
    *,
    tenant_id: str = "tenant_001",
    worker_id: str = "rca-worker-001",
) -> ClaimWorkflowRunCommand:
    """构造消费侧抢占命令。"""
    return ClaimWorkflowRunCommand(
        tenant_id=tenant_id,
        workflow_run_id="wfr_claim_001",
        worker_id=worker_id,
    )


def build_heartbeat_command(
    *,
    tenant_id: str = "tenant_001",
    worker_id: str = "rca-worker-001",
) -> HeartbeatWorkflowRunCommand:
    """构造运行中任务的续租命令。"""
    return HeartbeatWorkflowRunCommand(
        tenant_id=tenant_id,
        workflow_run_id="wfr_claim_001",
        worker_id=worker_id,
    )


def build_complete_command(
    *,
    tenant_id: str = "tenant_001",
    worker_id: str = "rca-worker-001",
    execution_attempt: int = 1,
    target_status: WorkflowRunStatus = WorkflowRunStatus.SUCCEEDED,
    evidence: tuple[Evidence, ...] = (),
    invocations: tuple[ToolInvocation, ...] = (),
    report: RCAReport | None = None,
) -> CompleteWorkflowRunCommand:
    """构造执行器提交终态的命令。"""
    return CompleteWorkflowRunCommand(
        tenant_id=tenant_id,
        workflow_run_id="wfr_claim_001",
        worker_id=worker_id,
        execution_attempt=execution_attempt,
        target_status=target_status,
        evidence=evidence,
        invocations=invocations,
        report=report,
    )


def build_evidence_for_completion() -> Evidence:
    """构造完成事务内需要保存的 RCA 证据。"""
    content_json, content_sha256 = build_evidence_content(
        {"source": "loki", "status": "ok"}
    )
    return Evidence(
        evidence_id="f" * 64,
        tenant_id="tenant_001",
        incident_id="inc_claim_001",
        workflow_run_id="wfr_claim_001",
        execution_attempt=1,
        step_id="collect.logs",
        tool_name="logs.query",
        tool_version="v1",
        evidence_type=EvidenceType.LOG,
        source="loki",
        summary="logs evidence collected",
        content_json=content_json,
        content_sha256=content_sha256,
        confidence=1.0,
        collected_at=NOW + timedelta(minutes=1),
    )


def build_invocation_for_completion(
    invocation_id: str = "d" * 64,
    *,
    step_id: str = "collect.logs",
) -> ToolInvocation:
    """构造完成事务内需要保存的工具调用审计记录。"""
    return ToolInvocation(
        invocation_id=invocation_id,
        tenant_id="tenant_001",
        incident_id="inc_claim_001",
        workflow_run_id="wfr_claim_001",
        execution_attempt=1,
        step_id=step_id,
        operator_id="operator_001",
        trace_id="trc_claim_001",
        tool_name="logs.query",
        tool_version="v1",
        risk_level=ToolRiskLevel.LOW,
        status=ToolInvocationStatus.SUCCEEDED,
        input_summary="payload_fields=10",
        input_sha256="a" * 64,
        output_summary="logs evidence collected",
        output_sha256="b" * 64,
        latency_ms=20,
        error_code=None,
        started_at=NOW + timedelta(seconds=10),
        ended_at=NOW + timedelta(seconds=10, milliseconds=20),
    )


def build_report_for_completion(evidence: Evidence) -> RCAReport:
    """构造完成事务内需要保存的确定性报告。"""
    return RCAReport(
        report_id="9" * 64,
        tenant_id="tenant_001",
        incident_id="inc_claim_001",
        workflow_run_id="wfr_claim_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.UNDETERMINED,
        title="Root cause requires human review",
        summary="No verified root cause candidate was produced.",
        confidence=0.0,
        evidence_ids=(evidence.evidence_id,),
        evidence_type_counts=(("LOG", 1),),
        recommendations=("Review cited evidence.",),
        generator_name="deterministic-evidence-summary",
        generator_version="v1",
        generated_at=NOW + timedelta(minutes=1),
    )
async def seed_pending_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """在同一准备事务中写入事故和待执行工作流。"""
    incident = Incident(
        incident_id="inc_claim_001",
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.ANALYZING,
        title="Checkout API failure",
        created_at=NOW,
        updated_at=NOW,
    )
    workflow_run = WorkflowRun(
        workflow_run_id="wfr_claim_001",
        tenant_id="tenant_001",
        incident_id="inc_claim_001",
        operator_id="operator_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_claim_001",
        status=WorkflowRunStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
        started_at=None,
        ended_at=None,
        step_count=0,
    )
    async with session_factory() as session:
        await SQLAlchemyIncidentRepository(session).save(incident)
        await SQLAlchemyWorkflowRunRepository(session).save(workflow_run)
        await session.commit()


def build_service(
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime,
) -> WorkflowExecutionApplicationService:
    """使用真实Unit of Work和固定时钟构造执行服务。"""
    return WorkflowExecutionApplicationService(
        unit_of_work_factory=lambda: SQLAlchemyUnitOfWork(session_factory),
        config=WorkflowClaimConfig(lease_duration=timedelta(minutes=2)),
        clock=lambda: now,
    )


async def test_service_claims_pending_workflow_and_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """应用服务应提交抢占结果，并返回原始链路标识。"""
    await seed_pending_workflow(session_factory)
    claim_time = NOW + timedelta(seconds=1)

    result = await build_service(
        session_factory,
        claim_time,
    ).claim(build_command())

    assert result.acquired is True
    assert result.status == WorkflowRunStatus.RUNNING.value
    assert result.lease_owner == "rca-worker-001"
    assert result.lease_expires_at == claim_time + timedelta(minutes=2)
    assert result.execution_attempts == 1
    assert result.trace_id == "trc_claim_001"

    async with session_factory() as session:
        stored = await SQLAlchemyWorkflowRunRepository(session).get_by_id(
            "tenant_001",
            "wfr_claim_001",
        )
    assert stored is not None
    assert stored.lease_owner == "rca-worker-001"


async def test_duplicate_delivery_does_not_start_second_executor(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """租约未过期时重复消息返回未获得执行权。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())

    duplicate = await build_service(
        session_factory,
        NOW + timedelta(minutes=1),
    ).claim(build_command(worker_id="rca-worker-002"))

    assert duplicate.acquired is False
    assert duplicate.status == WorkflowRunStatus.RUNNING.value
    assert duplicate.lease_owner == "rca-worker-001"
    assert duplicate.execution_attempts == 1


async def test_canceled_workflow_cannot_be_claimed_by_stale_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """控制面取消后，Kafka里的旧请求消息不能重新获得执行租约。"""
    await seed_pending_workflow(session_factory)
    canceled_at = NOW + timedelta(seconds=1)
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        workflow = await repository.get_by_id("tenant_001", "wfr_claim_001")
        assert workflow is not None
        workflow.cancel(
            canceled_at,
            canceled_by="admin_001",
            reason="Operator canceled stale RCA.",
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            trace_id="trc_cancel_001",
        )
        await repository.save(workflow)
        await session.commit()

    result = await build_service(
        session_factory,
        canceled_at + timedelta(seconds=1),
    ).claim(build_command(worker_id="rca-worker-002"))

    assert result.acquired is False
    assert result.status == WorkflowRunStatus.CANCELED.value
    assert result.lease_owner is None
    assert result.lease_expires_at is None
    assert result.execution_attempts == 0


async def test_expired_lease_is_reassigned_to_new_executor(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """原执行器失联后，新执行器可以接管并增加执行次数。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())

    reclaimed = await build_service(
        session_factory,
        NOW + timedelta(minutes=3),
    ).claim(build_command(worker_id="rca-worker-002"))

    assert reclaimed.acquired is True
    assert reclaimed.lease_owner == "rca-worker-002"
    assert reclaimed.execution_attempts == 2


async def test_missing_or_cross_tenant_workflow_is_not_exposed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """不存在或租户不匹配统一返回资源不存在。"""
    await seed_pending_workflow(session_factory)
    service = build_service(session_factory, NOW + timedelta(seconds=1))

    with pytest.raises(ResourceNotFound, match="Workflow run not found"):
        await service.claim(build_command(tenant_id="tenant_002"))


async def test_claim_configuration_and_clock_are_validated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """阻止危险租约配置和无时区时钟进入抢占流程。"""
    with pytest.raises(AppValidationError, match="between 10 seconds"):
        WorkflowClaimConfig(lease_duration=timedelta(seconds=1))

    service = WorkflowExecutionApplicationService(
        unit_of_work_factory=lambda: SQLAlchemyUnitOfWork(session_factory),
        clock=lambda: datetime(2026, 6, 28, 14, 0),
    )
    with pytest.raises(AppValidationError, match="timezone-aware"):
        await service.claim(build_command())


async def test_service_renews_current_worker_lease_and_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """当前执行器心跳应提交新租约，并保留执行次数。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())
    heartbeat_at = NOW + timedelta(minutes=1)

    result = await build_service(
        session_factory,
        heartbeat_at,
    ).heartbeat(build_heartbeat_command())

    assert result.lease_owner == "rca-worker-001"
    assert result.heartbeat_at == heartbeat_at
    assert result.lease_expires_at == heartbeat_at + timedelta(minutes=2)
    assert result.execution_attempts == 1
    assert result.trace_id == "trc_claim_001"


@pytest.mark.parametrize(
    ("worker_id", "heartbeat_at"),
    [
        ("rca-worker-002", NOW + timedelta(minutes=1)),
        ("rca-worker-001", NOW + timedelta(minutes=3)),
    ],
)
async def test_service_requires_active_lease_ownership(
    session_factory: async_sessionmaker[AsyncSession],
    worker_id: str,
    heartbeat_at: datetime,
) -> None:
    """错误所有者或过期执行器必须收到明确的失权异常。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())

    with pytest.raises(WorkflowLeaseLostError):
        await build_service(
            session_factory,
            heartbeat_at,
        ).heartbeat(build_heartbeat_command(worker_id=worker_id))


async def test_heartbeat_hides_cross_tenant_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """跨租户续租按资源不存在处理，不能泄漏租约状态。"""
    await seed_pending_workflow(session_factory)
    service = build_service(session_factory, NOW + timedelta(minutes=1))

    with pytest.raises(ResourceNotFound, match="Workflow run not found"):
        await service.heartbeat(
            build_heartbeat_command(tenant_id="tenant_002")
        )


async def test_service_completes_workflow_and_replays_idempotently(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """首次完成提交终态，重复命令返回原始结果且不改结束时间。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())
    completed_at = NOW + timedelta(minutes=1)
    evidence = build_evidence_for_completion()
    invocation = build_invocation_for_completion()
    report = build_report_for_completion(evidence)
    command = build_complete_command(
        evidence=(evidence,),
        invocations=(invocation,),
        report=report,
    )

    first = await build_service(
        session_factory,
        completed_at,
    ).complete(command)
    duplicate = await build_service(
        session_factory,
        completed_at + timedelta(seconds=30),
    ).complete(command)

    assert first.status == WorkflowRunStatus.SUCCEEDED.value
    assert first.ended_at == completed_at
    assert first.is_duplicate is False
    assert duplicate.ended_at == completed_at
    assert duplicate.is_duplicate is True

    async with session_factory() as session:
        stored_evidence = await SQLAlchemyEvidenceRepository(session).get_by_id(
            "tenant_001",
            evidence.evidence_id,
        )
        stored_invocations = (
            await SQLAlchemyToolInvocationRepository(
                session
            ).list_by_workflow_run("tenant_001", "wfr_claim_001")
        )
        stored_report = await SQLAlchemyRCAReportRepository(
            session
        ).get_by_workflow_run("tenant_001", "wfr_claim_001")
    assert stored_evidence is not None
    assert stored_evidence.workflow_run_id == "wfr_claim_001"
    assert [item.invocation_id for item in stored_invocations] == [
        invocation.invocation_id
    ]
    assert stored_report == report


async def test_invocation_conflict_rolls_back_workflow_and_evidence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """审计写入冲突时，工作流终态和 Evidence 必须整体回滚。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())
    evidence = build_evidence_for_completion()
    first = build_invocation_for_completion()
    duplicate_step = build_invocation_for_completion("e" * 64)
    async with session_factory() as session:
        await SQLAlchemyToolInvocationRepository(session).save(first)
        await session.commit()

    command = CompleteWorkflowRunCommand(
        tenant_id="tenant_001",
        workflow_run_id="wfr_claim_001",
        worker_id="rca-worker-001",
        execution_attempt=1,
        target_status=WorkflowRunStatus.SUCCEEDED,
        evidence=(evidence,),
        invocations=(duplicate_step,),
    )
    with pytest.raises(ConflictError):
        await build_service(
            session_factory,
            NOW + timedelta(minutes=1),
        ).complete(command)

    async with session_factory() as session:
        workflow = await SQLAlchemyWorkflowRunRepository(session).get_by_id(
            "tenant_001",
            "wfr_claim_001",
        )
        stored_evidence = await SQLAlchemyEvidenceRepository(session).get_by_id(
            "tenant_001",
            evidence.evidence_id,
        )
        stored_invocations = (
            await SQLAlchemyToolInvocationRepository(
                session
            ).list_by_workflow_run("tenant_001", "wfr_claim_001")
        )
    assert workflow is not None
    assert workflow.status is WorkflowRunStatus.RUNNING
    assert stored_evidence is None
    assert [item.invocation_id for item in stored_invocations] == [
        first.invocation_id
    ]


async def test_cross_incident_report_rolls_back_completion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """报告事故归属不一致时，终态与所有产物都不能提交。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())
    evidence = build_evidence_for_completion()
    invalid_report = replace(
        build_report_for_completion(evidence),
        incident_id="inc_other",
    )

    with pytest.raises(AppValidationError, match="report incident_id"):
        await build_service(
            session_factory,
            NOW + timedelta(minutes=1),
        ).complete(
            build_complete_command(
                evidence=(evidence,),
                report=invalid_report,
            )
        )

    async with session_factory() as session:
        workflow = await SQLAlchemyWorkflowRunRepository(session).get_by_id(
            "tenant_001",
            "wfr_claim_001",
        )
        stored_report = await SQLAlchemyRCAReportRepository(
            session
        ).get_by_workflow_run("tenant_001", "wfr_claim_001")
        stored_evidence = await SQLAlchemyEvidenceRepository(session).get_by_id(
            "tenant_001",
            evidence.evidence_id,
        )
    assert workflow is not None
    assert workflow.status is WorkflowRunStatus.RUNNING
    assert stored_report is None
    assert stored_evidence is None


@pytest.mark.parametrize(
    ("worker_id", "attempt", "completed_at"),
    [
        ("rca-worker-002", 1, NOW + timedelta(minutes=1)),
        ("rca-worker-001", 2, NOW + timedelta(minutes=1)),
        ("rca-worker-001", 1, NOW + timedelta(minutes=3)),
    ],
)
async def test_service_rejects_stale_completion(
    session_factory: async_sessionmaker[AsyncSession],
    worker_id: str,
    attempt: int,
    completed_at: datetime,
) -> None:
    """错误所有者、旧代次和过期执行结果都收到fencing拒绝。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())

    with pytest.raises(WorkflowLeaseLostError, match="fencing"):
        await build_service(
            session_factory,
            completed_at,
        ).complete(
            build_complete_command(
                worker_id=worker_id,
                execution_attempt=attempt,
                target_status=WorkflowRunStatus.FAILED,
            )
        )


async def test_completion_hides_cross_tenant_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """跨租户终态提交统一按资源不存在处理。"""
    await seed_pending_workflow(session_factory)

    with pytest.raises(ResourceNotFound, match="Workflow run not found"):
        await build_service(
            session_factory,
            NOW + timedelta(minutes=1),
        ).complete(build_complete_command(tenant_id="tenant_002"))


async def test_old_attempt_is_fenced_after_same_worker_reclaims(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """即使Worker标识复用，旧执行代次也不能覆盖接管后的任务。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())
    reclaimed = await build_service(
        session_factory,
        NOW + timedelta(minutes=3),
    ).claim(build_command())
    assert reclaimed.execution_attempts == 2

    with pytest.raises(WorkflowLeaseLostError, match="fencing"):
        await build_service(
            session_factory,
            NOW + timedelta(minutes=4),
        ).complete(build_complete_command(execution_attempt=1))

    completed = await build_service(
        session_factory,
        NOW + timedelta(minutes=4),
    ).complete(build_complete_command(execution_attempt=2))
    assert completed.status == WorkflowRunStatus.SUCCEEDED.value


async def test_replayed_completion_cannot_change_terminal_outcome(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """相同代次提交不同终态属于冲突，不能按幂等请求处理。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())
    await build_service(
        session_factory,
        NOW + timedelta(minutes=1),
    ).complete(build_complete_command())

    with pytest.raises(WorkflowLeaseLostError, match="fencing"):
        await build_service(
            session_factory,
            NOW + timedelta(minutes=1, seconds=30),
        ).complete(
            build_complete_command(
                target_status=WorkflowRunStatus.FAILED,
            )
        )


async def test_service_can_complete_current_attempt_as_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """当前有效执行代次可以明确写入失败终态。"""
    await seed_pending_workflow(session_factory)
    await build_service(
        session_factory,
        NOW + timedelta(seconds=1),
    ).claim(build_command())

    result = await build_service(
        session_factory,
        NOW + timedelta(minutes=1),
    ).complete(
        build_complete_command(target_status=WorkflowRunStatus.FAILED)
    )

    assert result.status == WorkflowRunStatus.FAILED.value
    assert result.is_duplicate is False
