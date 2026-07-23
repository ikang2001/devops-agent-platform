import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.ticket_drafts import (
    CreateTicketDraftCommand,
    DecideTicketDraftCommand,
    SubmitTicketDraftCommand,
)
from devops_agent_platform.application.services import (
    ticket_submission_record_processor as record_processor,
)
from devops_agent_platform.application.services import (
    ticket_submission_requested_handler as requested_handler,
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
    TicketSubmissionStatus,
    WorkflowRunStatus,
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
from devops_agent_platform.infrastructure.database.mappers.rca_report import (
    RCAReportMapper,
)
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)
from devops_agent_platform.infrastructure.database.models.ticket_submission import (
    TicketSubmissionRecord,
)
from devops_agent_platform.infrastructure.metrics import ApplicationMetrics
from devops_agent_platform.infrastructure.ticketing import (
    HttpJsonTicketingGateway,
    HttpJsonTicketingGatewayConfig,
)

NOW = datetime(2026, 7, 1, 20, 30, tzinfo=UTC)


class DeterministicIdentifiers:
    """生成稳定 ID，让端到端断言不依赖随机 UUID。"""

    def __init__(self) -> None:
        self.ticket_draft_sequence = 0
        self.ticket_submission_sequence = 0
        self.event_sequence = 0

    def new_ticket_draft_id(self) -> str:
        """生成草稿 ID。"""
        self.ticket_draft_sequence += 1
        return f"tdf_{self.ticket_draft_sequence:03d}"

    def new_ticket_submission_id(self) -> str:
        """生成外部提交请求 ID。"""
        self.ticket_submission_sequence += 1
        return f"tsb_{self.ticket_submission_sequence:03d}"

    def new_event_id(self) -> str:
        """生成 Outbox 事件 ID。"""
        self.event_sequence += 1
        return f"evt_{self.event_sequence:03d}"


class StepClock:
    """测试用单调时钟，避免真实时间导致指标断言不稳定。"""

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self._last = values[-1] if values else 0.0

    def __call__(self) -> float:
        """按顺序返回预设时间，耗尽后复用最后一个值。"""
        if self._values:
            self._last = self._values.pop(0)
        return self._last


@pytest.fixture
async def session_factory() -> AsyncIterator[
    async_sessionmaker[AsyncSession]
]:
    """创建包含事故、成功工作流和 RCA 报告的隔离数据库。"""
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
    """构造能推导 P1 工单草稿的事故。"""
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
    """构造已成功结束的 RCA 工作流。"""
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
    """构造含证据和建议的 RCA 报告，供草稿服务派生工单内容。"""
    return RCAReport(
        report_id="rpt_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.CANDIDATE,
        title="Checkout dependency timeout",
        summary="Payment dependency latency increased.",
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


def unit_of_work_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], SQLAlchemyUnitOfWork]:
    """返回每次调用都创建新事务边界的工厂。"""

    def factory() -> SQLAlchemyUnitOfWork:
        return SQLAlchemyUnitOfWork(session_factory)

    return factory


def build_draft_service(
    session_factory: async_sessionmaker[AsyncSession],
    identifiers: DeterministicIdentifiers,
) -> TicketDraftApplicationService:
    """组装真实草稿应用服务。"""
    return TicketDraftApplicationService(
        unit_of_work_factory(session_factory),
        identifiers,
        clock=lambda: NOW,
    )


def build_submission_service(
    session_factory: async_sessionmaker[AsyncSession],
    identifiers: DeterministicIdentifiers,
) -> TicketSubmissionApplicationService:
    """组装真实外部提交应用服务。"""
    return TicketSubmissionApplicationService(
        unit_of_work_factory(session_factory),
        identifiers,
        clock=lambda: NOW,
    )


async def prepare_requested_submission(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[DeterministicIdentifiers, TicketSubmissionRecord, OutboxEventRecord]:
    """沿真实应用入口创建、审批并登记一个待提交工单请求。"""
    identifiers = DeterministicIdentifiers()
    draft_service = build_draft_service(session_factory, identifiers)
    submission_service = build_submission_service(session_factory, identifiers)

    await draft_service.create(
        CreateTicketDraftCommand(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            idempotency_key="idem_ticket_001",
            requested_by="admin_001",
            trace_id="trc_ticket_001",
        )
    )
    await draft_service.decide(
        DecideTicketDraftCommand(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            decision=TicketDecision.APPROVE,
            reason=None,
            expected_version=1,
            idempotency_key="idem_decision_001",
            requested_by="approver_001",
            trace_id="trc_decision_001",
        )
    )
    await submission_service.request_submission(
        SubmitTicketDraftCommand(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            target_system="jira",
            expected_draft_version=2,
            idempotency_key="idem_submission_001",
            requested_by="submitter_001",
            trace_id="trc_submission_001",
        )
    )
    async with session_factory() as session:
        submission = await session.scalar(select(TicketSubmissionRecord))
        event = await session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.event_type == "ticket_submission.requested"
            )
        )
    assert submission is not None
    assert event is not None
    return identifiers, submission, event


def encode_outbox_event(event: OutboxEventRecord) -> bytes:
    """把真实 Outbox 记录编码成 Consumer 接收到的消息 Envelope。"""
    envelope = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "tenant_id": event.tenant_id,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "occurred_at": event.occurred_at.isoformat(),
        "trace_id": event.trace_id,
        "payload": event.payload,
    }
    return json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def build_processor(
    session_factory: async_sessionmaker[AsyncSession],
    identifiers: DeterministicIdentifiers,
    gateway: HttpJsonTicketingGateway,
) -> record_processor.TicketSubmissionRequestedRecordProcessor:
    """组装真实消息处理器，只替换外部工单 HTTP 端点。"""
    submission_service = build_submission_service(session_factory, identifiers)
    handler = requested_handler.TicketSubmissionRequestedMessageHandler(
        unit_of_work_factory=unit_of_work_factory(session_factory),
        ticketing_gateway=gateway,
        submission_service=submission_service,
        worker_id="ticket-worker-001",
    )
    return record_processor.TicketSubmissionRequestedRecordProcessor(handler)


def build_gateway(
    metrics: ApplicationMetrics,
    transport: httpx.MockTransport,
    clock: StepClock,
) -> tuple[HttpJsonTicketingGateway, httpx.AsyncClient]:
    """创建带 MockTransport 和指标观察者的 HTTP JSON 工单网关。"""
    client = httpx.AsyncClient(transport=transport)
    gateway = HttpJsonTicketingGateway(
        HttpJsonTicketingGatewayConfig(
            endpoint_url="https://ticketing.example/api/tickets",
            bearer_token=SecretStr("token_001"),
            request_timeout_seconds=2,
        ),
        http_client=client,
        observer=metrics,
        monotonic_clock=clock,
    )
    return gateway, client


async def read_submission(
    session_factory: async_sessionmaker[AsyncSession],
) -> TicketSubmissionRecord:
    """读取唯一提交记录。"""
    async with session_factory() as session:
        submission = await session.scalar(select(TicketSubmissionRecord))
    assert submission is not None
    return submission


async def count_outbox_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """统计 Outbox 事件总数。"""
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(OutboxEventRecord)
            )
            or 0
        )


async def count_ticket_submissions(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """统计外部提交请求记录总数。"""
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(TicketSubmissionRecord)
            )
            or 0
        )


async def test_submission_message_success_calls_gateway_and_records_metrics(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """成功提交应 ACK 消息、落终态、写结果事件并记录成功指标。"""
    identifiers, _, event = await prepare_requested_submission(session_factory)
    metrics = ApplicationMetrics()
    captured_requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(
            {
                "url": str(request.url),
                "idempotency_key": request.headers["Idempotency-Key"],
                "trace_id": request.headers["X-Trace-Id"],
                "authorization": request.headers["Authorization"],
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            json={
                "succeeded": True,
                "external_ticket_id": "JIRA-101",
                "external_ticket_url": "https://jira.example/browse/JIRA-101",
            },
        )

    gateway, client = build_gateway(
        metrics,
        httpx.MockTransport(handler),
        StepClock(10.0, 10.75),
    )
    async with client:
        result = await build_processor(
            session_factory,
            identifiers,
            gateway,
        ).process(encode_outbox_event(event))

    submission = await read_submission(session_factory)

    assert (
        result.disposition
        is record_processor.TicketSubmissionMessageDisposition.ACK
    )
    assert result.handling is not None
    assert result.handling.external_ticket_id == "JIRA-101"
    assert submission.status == TicketSubmissionStatus.SUBMITTED.value
    assert submission.version == 2
    assert submission.external_ticket_id == "JIRA-101"
    assert submission.result_trace_id == "trc_submission_001"
    assert await count_outbox_events(session_factory) == 4
    assert len(captured_requests) == 1
    captured = captured_requests[0]
    assert captured["url"] == "https://ticketing.example/api/tickets"
    assert captured["idempotency_key"] == event.event_id
    assert captured["trace_id"] == event.trace_id
    assert captured["authorization"] == "Bearer token_001"
    assert captured["body"] == {
        "tenant_id": "tenant_001",
        "ticket_submission_id": "tsb_001",
        "ticket_draft_id": "tdf_001",
        "target_system": "jira",
        "title": "Checkout dependency timeout",
        "description": captured["body"]["description"],
        "priority": "P1",
        "evidence_ids": ["evd_001", "evd_002"],
        "recommendations": [
            "Confirm dependency health.",
            "Prepare a rollback plan.",
        ],
    }
    assert "RCA conclusion: CANDIDATE" in captured["body"]["description"]
    assert "Payment dependency latency increased." in (
        captured["body"]["description"]
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "SUCCESS"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_duration_seconds_sum",
            {"outcome": "SUCCESS"},
        )
        == 0.75
    )


async def test_business_failure_is_acknowledged_and_recorded_as_final_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """外部系统业务拒绝应落 FAILED 终态，避免同一消息无意义重试。"""
    identifiers, _, event = await prepare_requested_submission(session_factory)
    metrics = ApplicationMetrics()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "succeeded": False,
                "failure_reason": "remote ticket validation failed",
            },
        )

    gateway, client = build_gateway(
        metrics,
        httpx.MockTransport(handler),
        StepClock(20.0, 20.5),
    )
    async with client:
        result = await build_processor(
            session_factory,
            identifiers,
            gateway,
        ).process(encode_outbox_event(event))

    submission = await read_submission(session_factory)

    assert (
        result.disposition
        is record_processor.TicketSubmissionMessageDisposition.ACK
    )
    assert submission.status == TicketSubmissionStatus.FAILED.value
    assert submission.failure_reason == "remote ticket validation failed"
    assert await count_outbox_events(session_factory) == 4
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "BUSINESS_FAILURE"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_duration_seconds_sum",
            {"outcome": "BUSINESS_FAILURE"},
        )
        == 0.5
    )


async def test_gateway_error_retries_without_finalizing_submission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """网关异常应保留 REQUESTED 状态并让消息重试。"""
    identifiers, _, event = await prepare_requested_submission(session_factory)
    metrics = ApplicationMetrics()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={"error": "temporarily unavailable"})

    gateway, client = build_gateway(
        metrics,
        httpx.MockTransport(handler),
        StepClock(30.0, 30.25),
    )
    async with client:
        result = await build_processor(
            session_factory,
            identifiers,
            gateway,
        ).process(encode_outbox_event(event))

    submission = await read_submission(session_factory)

    assert (
        result.disposition
        is record_processor.TicketSubmissionMessageDisposition.RETRY
    )
    assert result.reason_code == "TICKETING_GATEWAY_UNAVAILABLE"
    assert submission.status == TicketSubmissionStatus.REQUESTED.value
    assert submission.version == 1
    assert submission.external_ticket_id is None
    assert await count_outbox_events(session_factory) == 3
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "GATEWAY_ERROR"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_duration_seconds_sum",
            {"outcome": "GATEWAY_ERROR"},
        )
        == 0.25
    )


async def test_contract_mismatch_is_dead_lettered_without_gateway_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """消息与本地事实不一致时应进入死信，不能尝试外部建单。"""
    identifiers, _, event = await prepare_requested_submission(session_factory)
    bad_payload = dict(event.payload)
    bad_payload["draft_version"] = 99
    event.payload = bad_payload

    async def fail_if_called(request: httpx.Request) -> httpx.Response:
        del request
        raise AssertionError("bad message must not call gateway")

    metrics = ApplicationMetrics()
    gateway, client = build_gateway(
        metrics,
        httpx.MockTransport(fail_if_called),
        StepClock(80.0, 80.1),
    )
    async with client:
        result = await build_processor(
            session_factory,
            identifiers,
            gateway,
        ).process(encode_outbox_event(event))

    submission = await read_submission(session_factory)

    assert (
        result.disposition
        is record_processor.TicketSubmissionMessageDisposition.DEAD_LETTER
    )
    assert result.reason_code == "MESSAGE_CONTRACT_INVALID"
    assert submission.status == TicketSubmissionStatus.REQUESTED.value
    assert submission.version == 1
    assert await count_outbox_events(session_factory) == 3
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "SUCCESS"},
        )
        == 0
    )


async def test_missing_submission_record_is_dead_lettered_without_gateway_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Outbox 引用的本地提交记录缺失时应进入死信，等待人工修复。"""
    identifiers, _, event = await prepare_requested_submission(session_factory)
    async with session_factory() as session:
        await session.execute(delete(TicketSubmissionRecord))
        await session.commit()

    async def fail_if_called(request: httpx.Request) -> httpx.Response:
        del request
        raise AssertionError("missing local state must not call gateway")

    metrics = ApplicationMetrics()
    gateway, client = build_gateway(
        metrics,
        httpx.MockTransport(fail_if_called),
        StepClock(90.0, 90.1),
    )
    async with client:
        result = await build_processor(
            session_factory,
            identifiers,
            gateway,
        ).process(encode_outbox_event(event))

    assert (
        result.disposition
        is record_processor.TicketSubmissionMessageDisposition.DEAD_LETTER
    )
    assert result.reason_code == "TICKET_SUBMISSION_NOT_FOUND"
    assert await count_ticket_submissions(session_factory) == 0
    assert await count_outbox_events(session_factory) == 3
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "SUCCESS"},
        )
        == 0
    )


async def test_replayed_terminal_message_is_acknowledged_without_gateway_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """终态提交消息重放应直接 ACK，不能重复创建外部工单。"""
    identifiers, _, event = await prepare_requested_submission(session_factory)
    metrics = ApplicationMetrics()
    gateway_calls = 0

    async def success_handler(request: httpx.Request) -> httpx.Response:
        nonlocal gateway_calls
        gateway_calls += 1
        del request
        return httpx.Response(
            200,
            json={
                "succeeded": True,
                "external_ticket_id": "JIRA-101",
                "external_ticket_url": "https://jira.example/browse/JIRA-101",
            },
        )

    gateway, client = build_gateway(
        metrics,
        httpx.MockTransport(success_handler),
        StepClock(40.0, 40.4),
    )
    async with client:
        first = await build_processor(
            session_factory,
            identifiers,
            gateway,
        ).process(encode_outbox_event(event))

    async def fail_if_called(request: httpx.Request) -> httpx.Response:
        del request
        raise AssertionError("terminal replay must not call gateway")

    replay_metrics = ApplicationMetrics()
    replay_gateway, replay_client = build_gateway(
        replay_metrics,
        httpx.MockTransport(fail_if_called),
        StepClock(50.0, 50.1),
    )
    async with replay_client:
        replay = await build_processor(
            session_factory,
            identifiers,
            replay_gateway,
        ).process(encode_outbox_event(event))

    submission = await read_submission(session_factory)

    assert (
        first.disposition
        is record_processor.TicketSubmissionMessageDisposition.ACK
    )
    assert (
        replay.disposition
        is record_processor.TicketSubmissionMessageDisposition.ACK
    )
    assert replay.handling is not None
    assert replay.handling.disposition is (
        requested_handler.TicketSubmissionRequestedDisposition.IGNORED
    )
    assert gateway_calls == 1
    assert submission.status == TicketSubmissionStatus.SUBMITTED.value
    assert submission.external_ticket_id == "JIRA-101"
    assert await count_outbox_events(session_factory) == 4
    assert (
        replay_metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "SUCCESS"},
        )
        == 0
    )


async def test_gateway_retry_can_later_succeed_with_same_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """网关暂态失败后，同一消息重试成功应只产生一个最终结果。"""
    identifiers, _, event = await prepare_requested_submission(session_factory)
    metrics = ApplicationMetrics()
    call_count = 0

    async def flaky_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        del request
        if call_count == 1:
            return httpx.Response(
                503,
                json={"error": "temporarily unavailable"},
            )
        return httpx.Response(
            200,
            json={
                "succeeded": True,
                "external_ticket_id": "JIRA-202",
                "external_ticket_url": "https://jira.example/browse/JIRA-202",
            },
        )

    gateway, client = build_gateway(
        metrics,
        httpx.MockTransport(flaky_handler),
        StepClock(60.0, 60.2, 70.0, 70.6),
    )
    async with client:
        first = await build_processor(
            session_factory,
            identifiers,
            gateway,
        ).process(encode_outbox_event(event))
        after_retry = await read_submission(session_factory)

        second = await build_processor(
            session_factory,
            identifiers,
            gateway,
        ).process(encode_outbox_event(event))

    submission = await read_submission(session_factory)

    assert (
        first.disposition
        is record_processor.TicketSubmissionMessageDisposition.RETRY
    )
    assert first.reason_code == "TICKETING_GATEWAY_UNAVAILABLE"
    assert after_retry.status == TicketSubmissionStatus.REQUESTED.value
    assert after_retry.version == 1
    assert (
        second.disposition
        is record_processor.TicketSubmissionMessageDisposition.ACK
    )
    assert second.handling is not None
    assert second.handling.external_ticket_id == "JIRA-202"
    assert call_count == 2
    assert submission.status == TicketSubmissionStatus.SUBMITTED.value
    assert submission.external_ticket_id == "JIRA-202"
    assert submission.version == 2
    assert await count_outbox_events(session_factory) == 4
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "GATEWAY_ERROR"},
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "devops_agent_ticketing_gateway_submit_total",
            {"outcome": "SUCCESS"},
        )
        == 1
    )
    assert metrics.registry.get_sample_value(
        "devops_agent_ticketing_gateway_submit_duration_seconds_sum",
        {"outcome": "GATEWAY_ERROR"},
    ) == pytest.approx(0.2)
    assert metrics.registry.get_sample_value(
        "devops_agent_ticketing_gateway_submit_duration_seconds_sum",
        {"outcome": "SUCCESS"},
    ) == pytest.approx(0.6)
