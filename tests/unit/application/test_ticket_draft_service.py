import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.ticket_drafts import (
    CompleteTicketSubmissionCommand,
    CreateTicketDraftCommand,
    DecideTicketDraftCommand,
    SubmitTicketDraftCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.queries.ticket_drafts import (
    GetTicketDraftQuery,
    ListTicketSubmissionsQuery,
)
from devops_agent_platform.application.services.ticket_draft_service import (
    TicketDraftApplicationService,
)
from devops_agent_platform.application.services.ticket_submission_service import (
    TicketSubmissionApplicationService,
)
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    IncidentStatus,
    RCAConclusionStatus,
    TicketDecision,
    TicketDraftStatus,
    TicketSubmissionStatus,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyUnitOfWork,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)
from devops_agent_platform.infrastructure.database.mappers.outbox import (
    OutboxEventMapper,
)
from devops_agent_platform.infrastructure.database.mappers.rca_report import (
    RCAReportMapper,
)
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)
from devops_agent_platform.infrastructure.database.models.rca_report import (
    RCAReportRecord,
)
from devops_agent_platform.infrastructure.database.models.ticket_draft import (
    TicketDraftRecord,
)
from devops_agent_platform.infrastructure.database.models.ticket_submission import (
    TicketSubmissionRecord,
)
from devops_agent_platform.infrastructure.database.models.workflow_run import (
    WorkflowRunRecord,
)

NOW = datetime(2026, 7, 1, 16, 0, tzinfo=UTC)


class DeterministicIdentifiers:
    """生成可预测的草稿与审计事件标识。"""

    def __init__(self) -> None:
        self.ticket_sequence = 0
        self.submission_sequence = 0
        self.event_sequence = 0

    def new_ticket_draft_id(self) -> str:
        self.ticket_sequence += 1
        return f"tdf_{self.ticket_sequence:03d}"

    def new_event_id(self) -> str:
        self.event_sequence += 1
        return f"evt_{self.event_sequence:03d}"

    def new_ticket_submission_id(self) -> str:
        self.submission_sequence += 1
        return f"tsb_{self.submission_sequence:03d}"


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建包含成功工作流、事故和报告的隔离数据库。"""
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
    """构造 P1 映射所需的严重事故。"""
    return Incident(
        incident_id="inc_001",
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.ANALYZING,
        title="Checkout outage",
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
    )


def build_workflow() -> WorkflowRun:
    """构造已经成功且审计仍可用的工作流。"""
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="operator_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_workflow",
        status=WorkflowRunStatus.SUCCEEDED,
        created_at=NOW - timedelta(minutes=30),
        updated_at=NOW - timedelta(minutes=5),
        started_at=NOW - timedelta(minutes=25),
        ended_at=NOW - timedelta(minutes=5),
        step_count=4,
        execution_attempts=1,
    )


def build_report() -> RCAReport:
    """构造包含证据引用和建议的 RCA 报告。"""
    return RCAReport(
        report_id="rpt_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.CANDIDATE,
        title="Checkout dependency timeout",
        summary="Payment dependency latency increased.\nReview before action.",
        confidence=0.72,
        evidence_ids=("evd_001", "evd_002"),
        evidence_type_counts=(("LOG", 1), ("METRIC", 1)),
        recommendations=(
            "Confirm dependency health.",
            "Prepare a rollback plan.",
        ),
        generator_name="deterministic-evidence-summary",
        generator_version="v1",
        generated_at=NOW - timedelta(minutes=5),
    )


def build_service(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identifiers: DeterministicIdentifiers | None = None,
    clock=None,
) -> TicketDraftApplicationService:
    """组装真实 UoW 与可控协作者。"""

    def unit_of_work_factory() -> SQLAlchemyUnitOfWork:
        return SQLAlchemyUnitOfWork(session_factory)

    return TicketDraftApplicationService(
        unit_of_work_factory,
        identifiers or DeterministicIdentifiers(),
        clock=clock or (lambda: NOW),
    )


def build_submission_service(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identifiers: DeterministicIdentifiers | None = None,
    clock=None,
) -> TicketSubmissionApplicationService:
    """组装真实 UoW 与提交请求服务。"""

    def unit_of_work_factory() -> SQLAlchemyUnitOfWork:
        return SQLAlchemyUnitOfWork(session_factory)

    return TicketSubmissionApplicationService(
        unit_of_work_factory,
        identifiers or DeterministicIdentifiers(),
        clock=clock or (lambda: NOW),
    )


def command(
    *,
    idempotency_key: str = "idem_ticket_001",
    requested_by: str = "admin_001",
    trace_id: str = "trc_ticket_001",
) -> CreateTicketDraftCommand:
    """构造默认创建命令。"""
    return CreateTicketDraftCommand(
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        idempotency_key=idempotency_key,
        requested_by=requested_by,
        trace_id=trace_id,
    )


def decision_command(
    *,
    decision: TicketDecision = TicketDecision.APPROVE,
    reason: str | None = None,
    expected_version: int = 1,
    idempotency_key: str = "idem_decision_001",
    requested_by: str = "approver_001",
    trace_id: str = "trc_decision_001",
) -> DecideTicketDraftCommand:
    """构造默认人工确认命令。"""
    return DecideTicketDraftCommand(
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        decision=decision,
        reason=reason,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        requested_by=requested_by,
        trace_id=trace_id,
    )


def submit_command(
    *,
    target_system: str = "jira",
    expected_draft_version: int = 2,
    idempotency_key: str = "idem_submission_001",
    requested_by: str = "submitter_001",
    trace_id: str = "trc_submission_001",
) -> SubmitTicketDraftCommand:
    """构造默认外部提交请求命令。"""
    return SubmitTicketDraftCommand(
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        target_system=target_system,
        expected_draft_version=expected_draft_version,
        idempotency_key=idempotency_key,
        requested_by=requested_by,
        trace_id=trace_id,
    )


def complete_command(
    *,
    ticket_submission_id: str = "tsb_001",
    result_status: TicketSubmissionStatus = TicketSubmissionStatus.SUBMITTED,
    expected_version: int = 1,
    idempotency_key: str = "idem_submission_result_001",
    completed_by: str = "ticket_worker_001",
    trace_id: str = "trc_submission_result_001",
    external_ticket_id: str | None = "JIRA-101",
    external_ticket_url: str | None = "https://jira.example/browse/JIRA-101",
    failure_reason: str | None = None,
) -> CompleteTicketSubmissionCommand:
    """构造默认外部提交结果回填命令。"""
    return CompleteTicketSubmissionCommand(
        tenant_id="tenant_001",
        ticket_submission_id=ticket_submission_id,
        result_status=result_status,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        completed_by=completed_by,
        trace_id=trace_id,
        external_ticket_id=external_ticket_id,
        external_ticket_url=external_ticket_url,
        failure_reason=failure_reason,
    )


async def count_records(
    session_factory: async_sessionmaker[AsyncSession],
    model: type,
) -> int:
    """统计指定表记录数。"""
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_create_is_derived_atomic_and_queryable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """创建应派生 P1 草稿，并原子写入不含正文的审计事件。"""
    service = build_service(session_factory)

    result = await service.create(command())
    restored = await service.get(GetTicketDraftQuery("tenant_001", "wfr_001"))

    assert result.priority == "P1"
    assert result.title == "Checkout dependency timeout"
    assert "\n" in result.description
    assert result.evidence_ids == ("evd_001", "evd_002")
    assert restored.ticket_draft_id == result.ticket_draft_id
    assert await count_records(session_factory, TicketDraftRecord) == 1
    assert await count_records(session_factory, OutboxEventRecord) == 1

    async with session_factory() as session:
        event = await session.scalar(select(OutboxEventRecord))
    assert event is not None
    assert event.event_type == "ticket_draft.created"
    assert event.payload["evidence_count"] == 2
    assert "description" not in event.payload
    assert "recommendations" not in event.payload


async def test_create_redacts_report_text_before_ticket_draft_storage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史报告或替代生成器的敏感文本不能穿透到工单草稿。"""
    polluted_record = RCAReportMapper.to_record(build_report())
    polluted_record.title = "Checkout password=hunter2"
    polluted_record.summary = (
        "Provider token=secret-token needs\tmanual\x7freview."
    )
    polluted_record.recommendations_json = json.dumps(
        (
            "Rotate api_key=private-key before submit.",
            "Rotate api_key=other-key before submit.",
            "Notify\towner\x7fnow.",
        )
    )
    async with session_factory() as session:
        await session.execute(delete(RCAReportRecord))
        session.add(polluted_record)
        await session.commit()

    result = await build_service(session_factory).create(command())

    assert result.title == "Checkout password=[REDACTED]"
    assert (
        "Provider token=[REDACTED] "
        "needs\\u0009manual\\u007freview."
    ) in (
        result.description
    )
    assert result.recommendations == (
        "Rotate api_key=[REDACTED] before submit.",
        "Notify\\u0009owner\\u007fnow.",
    )
    assert "\x7f" not in repr(result)
    assert "hunter2" not in repr(result)
    assert "secret-token" not in repr(result)
    assert "private-key" not in repr(result)
    assert "other-key" not in repr(result)


async def test_same_request_replays_without_new_side_effects(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """相同幂等请求返回原草稿，不重复写 Outbox。"""
    service = build_service(session_factory)

    first = await service.create(command())
    replay = await service.create(command(trace_id="trc_ticket_retry"))

    assert replay.ticket_draft_id == first.ticket_draft_id
    assert replay.is_duplicate is True
    assert replay.trace_id == "trc_ticket_retry"
    assert await count_records(session_factory, TicketDraftRecord) == 1
    assert await count_records(session_factory, OutboxEventRecord) == 1


async def test_idempotency_key_reuse_by_other_request_conflicts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一幂等键不能被其他管理员创建意图复用。"""
    service = build_service(session_factory)
    await service.create(command())

    with pytest.raises(ConflictError, match="Idempotency key"):
        await service.create(command(requested_by="admin_002"))


async def test_second_key_for_same_workflow_conflicts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一工作流不能通过更换幂等键生成多个草稿。"""
    service = build_service(session_factory)
    await service.create(command())

    with pytest.raises(ConflictError, match="already exists"):
        await service.create(command(idempotency_key="idem_ticket_002"))

    assert await count_records(session_factory, TicketDraftRecord) == 1


async def test_outbox_failure_rolls_back_ticket_draft(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """审计事件冲突时草稿写入也必须整体回滚。"""
    async with session_factory() as session:
        session.add(
            OutboxEventMapper.to_record(
                OutboxEvent(
                    event_id="evt_001",
                    tenant_id="tenant_001",
                    aggregate_type="Existing",
                    aggregate_id="existing_001",
                    event_type="existing.event",
                    schema_version=1,
                    payload={},
                    occurred_at=NOW - timedelta(minutes=1),
                    trace_id="trc_existing",
                )
            )
        )
        await session.commit()

    with pytest.raises(ConflictError):
        await build_service(session_factory).create(command())

    assert await count_records(session_factory, TicketDraftRecord) == 0
    assert await count_records(session_factory, OutboxEventRecord) == 1


async def test_approve_is_terminal_and_audited(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """批准应推进版本，并写入不含正文的审计事件。"""
    identifiers = DeterministicIdentifiers()
    service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    await service.create(command())

    approved = await service.decide(decision_command())

    assert approved.status == TicketDraftStatus.APPROVED.value
    assert approved.version == 2
    assert approved.decided_by == "approver_001"
    assert approved.decision_reason is None
    assert approved.decision_trace_id == "trc_decision_001"
    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.event_type == "ticket_draft.approved"
            )
        )
    assert event is not None
    assert event.payload["decision_reason_sha256"] is None
    assert event.trace_id == "trc_decision_001"


async def test_rejection_reason_stays_out_of_outbox(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """拒绝原因保存在业务表，Outbox 只传播摘要。"""
    service = build_service(session_factory)
    await service.create(command())
    reason = "Evidence is insufficient for escalation."

    rejected = await service.decide(
        decision_command(
            decision=TicketDecision.REJECT,
            reason=reason,
        )
    )

    assert rejected.status == TicketDraftStatus.REJECTED.value
    assert rejected.decision_reason == reason
    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.event_type == "ticket_draft.rejected"
            )
        )
    assert event is not None
    assert event.payload["decision_reason_sha256"]
    assert reason not in str(event.payload)


async def test_rejection_reason_is_redacted_before_storage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """人工拒绝原因进入业务表前必须脱敏，Outbox 仍只传播摘要。"""
    identifiers = DeterministicIdentifiers()
    service = build_service(session_factory, identifiers=identifiers)
    await service.create(command())
    reason = "operator copied password=hunter2 token=secret-token"

    rejected = await service.decide(
        decision_command(
            decision=TicketDecision.REJECT,
            reason=reason,
        )
    )

    assert rejected.status == TicketDraftStatus.REJECTED.value
    assert rejected.decision_reason == (
        "operator copied password=[REDACTED] token=[REDACTED]"
    )
    assert "hunter2" not in str(rejected.decision_reason)
    assert "secret-token" not in str(rejected.decision_reason)
    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.event_type == "ticket_draft.rejected"
            )
        )
    assert event is not None
    assert event.payload["decision_reason_sha256"]
    assert "hunter2" not in str(event.payload)
    assert "secret-token" not in str(event.payload)


async def test_same_decision_replays_without_duplicate_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """审批网络重试应返回原终态，不重复发送审计事件。"""
    service = build_service(session_factory)
    await service.create(command())

    first = await service.decide(decision_command())
    replay = await service.decide(decision_command(trace_id="trc_decision_retry"))

    assert replay.status == first.status
    assert replay.is_duplicate is True
    assert replay.trace_id == "trc_decision_retry"
    assert await count_records(session_factory, OutboxEventRecord) == 2


async def test_terminal_decision_rejects_other_requests(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """终态后不能改判，也不能复用审批幂等键表达另一请求。"""
    service = build_service(session_factory)
    await service.create(command())
    await service.decide(decision_command())

    with pytest.raises(ConflictError, match="another ticket decision"):
        await service.decide(
            decision_command(
                decision=TicketDecision.REJECT,
                reason="Different decision.",
            )
        )
    with pytest.raises(ConflictError):
        await service.decide(
            decision_command(
                idempotency_key="idem_decision_002",
            )
        )
    assert await count_records(session_factory, OutboxEventRecord) == 2


async def test_stale_decision_version_fails_before_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """错误 If-Match 版本不得产生审批副作用。"""
    service = build_service(session_factory)
    await service.create(command())

    with pytest.raises(ConflictError, match="version"):
        await service.decide(
            decision_command(
                expected_version=2,
                idempotency_key="idem_stale_decision",
            )
        )

    restored = await service.get(GetTicketDraftQuery("tenant_001", "wfr_001"))
    assert restored.status == TicketDraftStatus.DRAFT.value
    assert await count_records(session_factory, OutboxEventRecord) == 1


async def test_decision_outbox_failure_rolls_back_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """审批审计写入失败时状态更新必须回滚为草稿。"""
    identifiers = DeterministicIdentifiers()
    service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    await service.create(command())
    async with session_factory() as session:
        session.add(
            OutboxEventMapper.to_record(
                OutboxEvent(
                    event_id="evt_002",
                    tenant_id="tenant_001",
                    aggregate_type="Existing",
                    aggregate_id="existing_002",
                    event_type="existing.event",
                    schema_version=1,
                    payload={},
                    occurred_at=NOW,
                    trace_id="trc_existing",
                )
            )
        )
        await session.commit()

    with pytest.raises(ConflictError):
        await service.decide(decision_command())

    restored = await service.get(GetTicketDraftQuery("tenant_001", "wfr_001"))
    assert restored.status == TicketDraftStatus.DRAFT.value
    assert restored.version == 1


async def test_approved_draft_can_request_external_submission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """已批准草稿可登记外部提交请求，并写入不含正文的 Outbox。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    approved = await draft_service.decide(decision_command())

    submitted = await submission_service.request_submission(submit_command())

    assert approved.version == 2
    assert submitted.ticket_submission_id == "tsb_001"
    assert submitted.status == TicketSubmissionStatus.REQUESTED.value
    assert submitted.target_system == "jira"
    assert submitted.version == 1
    assert await count_records(session_factory, TicketSubmissionRecord) == 1
    assert await count_records(session_factory, OutboxEventRecord) == 3
    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.event_type == "ticket_submission.requested"
            )
        )
    assert event is not None
    assert event.trace_id == "trc_submission_001"
    assert event.payload["target_system"] == "jira"
    assert "description" not in event.payload
    assert "recommendations" not in event.payload


async def test_submission_statuses_are_queryable_by_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """提交状态查询应按租户和工作流返回最新优先的有限快照。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_times = iter(
        (
            NOW + timedelta(minutes=10),
            NOW + timedelta(minutes=11),
        )
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
        clock=lambda: next(submission_times),
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    first = await submission_service.request_submission(
        submit_command(target_system="jira")
    )
    second = await submission_service.request_submission(
        submit_command(
            target_system="servicenow",
            idempotency_key="idem_submission_002",
            trace_id="trc_submission_002",
        )
    )

    listed = await submission_service.list_by_workflow(
        ListTicketSubmissionsQuery("tenant_001", "wfr_001", limit=10)
    )
    limited = await submission_service.list_by_workflow(
        ListTicketSubmissionsQuery("tenant_001", "wfr_001", limit=1)
    )
    foreign_tenant = await submission_service.list_by_workflow(
        ListTicketSubmissionsQuery("tenant_other", "wfr_001", limit=10)
    )

    assert first.ticket_submission_id == "tsb_001"
    assert second.ticket_submission_id == "tsb_002"
    assert [item.ticket_submission_id for item in listed] == [
        "tsb_002",
        "tsb_001",
    ]
    assert listed[0].target_system == "servicenow"
    assert listed[0].requested_at == NOW + timedelta(minutes=11)
    assert [item.ticket_submission_id for item in limited] == ["tsb_002"]
    assert foreign_tenant == ()


async def test_same_submission_request_replays_without_duplicate_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """外部提交请求重试应返回原结果，不重复写 Outbox。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())

    first = await submission_service.request_submission(submit_command())
    replay = await submission_service.request_submission(
        submit_command(trace_id="trc_submission_retry")
    )

    assert replay.ticket_submission_id == first.ticket_submission_id
    assert replay.is_duplicate is True
    assert replay.trace_id == "trc_submission_retry"
    assert await count_records(session_factory, TicketSubmissionRecord) == 1
    assert await count_records(session_factory, OutboxEventRecord) == 3


async def test_submission_requires_approved_current_draft(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """未批准、已拒绝或版本不匹配的草稿不得登记提交请求。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())

    with pytest.raises(ConflictError, match="approved"):
        await submission_service.request_submission(submit_command())

    await draft_service.decide(
        decision_command(
            decision=TicketDecision.REJECT,
            reason="Evidence is insufficient.",
        )
    )
    with pytest.raises(ConflictError, match="approved"):
        await submission_service.request_submission(
            submit_command(idempotency_key="idem_submission_002")
        )

    assert await count_records(session_factory, TicketSubmissionRecord) == 0


async def test_submission_stale_version_and_duplicate_target_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """错误版本或换幂等键向同一目标重复提交都必须失败。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())

    with pytest.raises(ConflictError, match="version"):
        await submission_service.request_submission(
            submit_command(
                expected_draft_version=1,
                idempotency_key="idem_submission_stale",
            )
        )
    await submission_service.request_submission(submit_command())
    with pytest.raises(ConflictError, match="already submitted"):
        await submission_service.request_submission(
            submit_command(idempotency_key="idem_submission_002")
        )

    assert await count_records(session_factory, TicketSubmissionRecord) == 1


async def test_submission_idempotency_key_reuse_conflicts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一幂等键不能被另一提交意图复用。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    await submission_service.request_submission(submit_command())

    with pytest.raises(ConflictError, match="Idempotency key"):
        await submission_service.request_submission(
            submit_command(
                target_system="servicenow",
            )
        )


async def test_submission_outbox_failure_rolls_back_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """提交请求审计写入失败时提交记录必须整体回滚。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    async with session_factory() as session:
        session.add(
            OutboxEventMapper.to_record(
                OutboxEvent(
                    event_id="evt_003",
                    tenant_id="tenant_001",
                    aggregate_type="Existing",
                    aggregate_id="existing_003",
                    event_type="existing.event",
                    schema_version=1,
                    payload={},
                    occurred_at=NOW,
                    trace_id="trc_existing",
                )
            )
        )
        await session.commit()

    with pytest.raises(ConflictError):
        await submission_service.request_submission(submit_command())

    assert await count_records(session_factory, TicketSubmissionRecord) == 0


async def test_submission_result_can_mark_external_ticket_submitted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """外部建单成功后应原子记录终态和审计事件。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    submitted_request = await submission_service.request_submission(submit_command())

    completed = await submission_service.record_result(
        complete_command(ticket_submission_id=submitted_request.ticket_submission_id)
    )

    assert completed.status == TicketSubmissionStatus.SUBMITTED.value
    assert completed.version == 2
    assert completed.external_ticket_id == "JIRA-101"
    assert completed.completed_by == "ticket_worker_001"
    assert completed.result_trace_id == "trc_submission_result_001"
    assert await count_records(session_factory, OutboxEventRecord) == 4
    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.event_type == "ticket_submission.submitted"
            )
        )
    assert event is not None
    assert event.trace_id == "trc_submission_result_001"
    assert event.payload["external_ticket_id"] == "JIRA-101"
    assert event.payload["failure_reason_sha256"] is None


async def test_submission_failed_result_keeps_reason_out_of_outbox(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """外部提交失败原因留在业务表，Outbox 只传播摘要。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    submitted_request = await submission_service.request_submission(submit_command())
    reason = "Jira API timeout with request id abc-123"

    completed = await submission_service.record_result(
        complete_command(
            ticket_submission_id=submitted_request.ticket_submission_id,
            result_status=TicketSubmissionStatus.FAILED,
            external_ticket_id=None,
            external_ticket_url=None,
            failure_reason=reason,
        )
    )

    assert completed.status == TicketSubmissionStatus.FAILED.value
    assert completed.failure_reason == reason
    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.event_type == "ticket_submission.failed"
            )
        )
    assert event is not None
    assert event.payload["failure_reason_sha256"]
    assert reason not in str(event.payload)


async def test_submission_view_hides_failure_reason_by_default(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """失败原因落库前先脱敏，接口字典默认只暴露摘要。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    submitted_request = await submission_service.request_submission(submit_command())
    reason = "provider error password=secret-token"

    completed = await submission_service.record_result(
        complete_command(
            ticket_submission_id=submitted_request.ticket_submission_id,
            result_status=TicketSubmissionStatus.FAILED,
            external_ticket_id=None,
            external_ticket_url=None,
            failure_reason=reason,
        )
    )

    safe = completed.to_dict()
    privileged = completed.to_dict(include_failure_reason=True)
    assert completed.failure_reason == "provider error password=[REDACTED]"
    assert safe["failure_reason"] is None
    assert len(safe["failure_reason_sha256"]) == 64
    assert reason not in str(safe)
    assert privileged["failure_reason"] == completed.failure_reason
    assert "secret-token" not in privileged["failure_reason"]


async def test_same_submission_result_replays_without_duplicate_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """外部结果回填重试应返回原终态，不重复写 Outbox。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    submitted_request = await submission_service.request_submission(submit_command())

    first = await submission_service.record_result(
        complete_command(ticket_submission_id=submitted_request.ticket_submission_id)
    )
    replay = await submission_service.record_result(
        complete_command(
            ticket_submission_id=submitted_request.ticket_submission_id,
            trace_id="trc_submission_result_retry",
        )
    )

    assert replay.status == first.status
    assert replay.is_duplicate is True
    assert replay.trace_id == "trc_submission_result_retry"
    assert await count_records(session_factory, OutboxEventRecord) == 4


async def test_terminal_submission_result_rejects_other_results(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """终态结果不能被改判，也不能复用结果幂等键表达另一请求。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    submitted_request = await submission_service.request_submission(submit_command())
    await submission_service.record_result(
        complete_command(ticket_submission_id=submitted_request.ticket_submission_id)
    )

    with pytest.raises(ConflictError, match="another ticket submission result"):
        await submission_service.record_result(
            complete_command(
                ticket_submission_id=submitted_request.ticket_submission_id,
                result_status=TicketSubmissionStatus.FAILED,
                external_ticket_id=None,
                external_ticket_url=None,
                failure_reason="Different result.",
            )
        )
    with pytest.raises(ConflictError, match="already final"):
        await submission_service.record_result(
            complete_command(
                ticket_submission_id=submitted_request.ticket_submission_id,
                idempotency_key="idem_submission_result_002",
            )
        )
    assert await count_records(session_factory, OutboxEventRecord) == 4


async def test_submission_result_stale_version_fails_before_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """错误结果版本不得产生终态副作用。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    submitted_request = await submission_service.request_submission(submit_command())

    with pytest.raises(ConflictError, match="version"):
        await submission_service.record_result(
            complete_command(
                ticket_submission_id=submitted_request.ticket_submission_id,
                expected_version=2,
            )
        )

    async with session_factory() as session:
        row = await session.get(
            TicketSubmissionRecord,
            submitted_request.ticket_submission_id,
        )
    assert row is not None
    assert row.status == TicketSubmissionStatus.REQUESTED.value
    assert await count_records(session_factory, OutboxEventRecord) == 3


async def test_submission_result_outbox_failure_rolls_back_terminal_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """结果审计写入失败时终态更新必须回滚为 REQUESTED。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    submission_service = build_submission_service(
        session_factory,
        identifiers=identifiers,
    )
    await draft_service.create(command())
    await draft_service.decide(decision_command())
    submitted_request = await submission_service.request_submission(submit_command())
    async with session_factory() as session:
        session.add(
            OutboxEventMapper.to_record(
                OutboxEvent(
                    event_id="evt_004",
                    tenant_id="tenant_001",
                    aggregate_type="Existing",
                    aggregate_id="existing_004",
                    event_type="existing.event",
                    schema_version=1,
                    payload={},
                    occurred_at=NOW,
                    trace_id="trc_existing",
                )
            )
        )
        await session.commit()

    with pytest.raises(ConflictError):
        await submission_service.record_result(
            complete_command(
                ticket_submission_id=submitted_request.ticket_submission_id
            )
        )

    async with session_factory() as session:
        row = await session.get(
            TicketSubmissionRecord,
            submitted_request.ticket_submission_id,
        )
    assert row is not None
    assert row.status == TicketSubmissionStatus.REQUESTED.value
    assert row.version == 1


@pytest.mark.parametrize(
    ("database_change", "message"),
    [
        (
            update(WorkflowRunRecord)
            .values(status=WorkflowRunStatus.FAILED.value)
            .where(WorkflowRunRecord.workflow_run_id == "wfr_001"),
            "succeeded",
        ),
        (
            update(WorkflowRunRecord)
            .values(audit_purged_at=NOW)
            .where(WorkflowRunRecord.workflow_run_id == "wfr_001"),
            "audit",
        ),
        (
            delete(RCAReportRecord).where(RCAReportRecord.report_id == "rpt_001"),
            "RCA report",
        ),
    ],
)
async def test_untrusted_source_state_cannot_create_draft(
    session_factory: async_sessionmaker[AsyncSession],
    database_change,
    message: str,
) -> None:
    """失败工作流、已清理审计或缺失报告都必须拒绝。"""
    async with session_factory() as session:
        await session.execute(database_change)
        await session.commit()

    with pytest.raises(ConflictError, match=message):
        await build_service(session_factory).create(command())

    assert await count_records(session_factory, TicketDraftRecord) == 0
    assert await count_records(session_factory, OutboxEventRecord) == 0


async def test_cross_tenant_query_is_not_found(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """跨租户读取与不存在草稿使用相同语义。"""
    service = build_service(session_factory)
    await service.create(command())

    with pytest.raises(ResourceNotFound):
        await service.get(GetTicketDraftQuery("tenant_other", "wfr_001"))


async def test_command_and_clock_reject_invalid_values(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """非法审计身份与无时区时钟应在写入前失败。"""
    with pytest.raises(AppValidationError):
        command(idempotency_key="contains whitespace")
    with pytest.raises(AppValidationError):
        command(trace_id="trc_ticket\x7fforged")
    with pytest.raises(AppValidationError):
        decision_command(
            decision=TicketDecision.REJECT,
            reason="Need owner review\x7fforged",
        )
    with pytest.raises(AppValidationError):
        submit_command(target_system="jira\x7fforged")
    with pytest.raises(AppValidationError):
        complete_command(external_ticket_id="JIRA-101\x7fforged")
    with pytest.raises(AppValidationError):
        complete_command(
            result_status=TicketSubmissionStatus.FAILED,
            external_ticket_id=None,
            external_ticket_url=None,
            failure_reason="Provider rejected\x7fforged",
        )
    with pytest.raises(AppValidationError, match="worker_id"):
        complete_command(completed_by="ticket worker")
    with pytest.raises(AppValidationError):
        ListTicketSubmissionsQuery("tenant_001", "wfr_001", limit=0)
    with pytest.raises(AppValidationError, match="workflow_run_id"):
        GetTicketDraftQuery("tenant_001", "wfr_001\nforged")
    with pytest.raises(AppValidationError, match="tenant_id"):
        ListTicketSubmissionsQuery(
            "tenant_001\x7fforged",
            "wfr_001",
            limit=10,
        )
    with pytest.raises(AppValidationError, match="workflow_run_id"):
        ListTicketSubmissionsQuery(
            "tenant_001",
            "wfr_001\x7fforged",
            limit=10,
        )
    service = build_service(
        session_factory,
        clock=lambda: datetime(2026, 7, 1, 16, 0),
    )
    with pytest.raises(AppValidationError, match="timezone-aware"):
        await service.create(command())
