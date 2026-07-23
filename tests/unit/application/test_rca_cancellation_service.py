from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.rca import (
    CancelRCAWorkflowCommand,
)
from devops_agent_platform.application.services.rca_cancellation_service import (
    RCACancellationService,
)
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    IncidentStatus,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyIncidentRepository,
    SQLAlchemyUnitOfWork,
    SQLAlchemyWorkflowRunRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)
from devops_agent_platform.infrastructure.database.models.workflow_run import (
    WorkflowRunRecord,
)

NOW = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)


class FixedIdentifiers:
    """为取消审计事件返回固定 ID。"""

    def new_event_id(self) -> str:
        return "evt_rca_canceled_001"


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建包含一个待执行 RCA 工作流的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        incident = Incident(
            incident_id="inc_001",
            tenant_id="tenant_001",
            service_name="checkout-api",
            severity=AlertSeverity.CRITICAL,
            status=IncidentStatus.ANALYZING,
            title="Checkout outage",
            created_at=NOW - timedelta(hours=1),
            updated_at=NOW - timedelta(minutes=10),
        )
        workflow_run = build_pending_workflow()
        await SQLAlchemyIncidentRepository(session).save(incident)
        await SQLAlchemyWorkflowRunRepository(session).save(workflow_run)
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


def build_pending_workflow() -> WorkflowRun:
    """构造可被管理员取消的待执行工作流。"""
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="operator_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_rca_001",
        status=WorkflowRunStatus.PENDING,
        created_at=NOW - timedelta(minutes=5),
        updated_at=NOW - timedelta(minutes=5),
        started_at=None,
        ended_at=None,
        step_count=0,
    )


def build_command(
    *,
    tenant_id: str = "tenant_001",
    expected_version: int = 1,
    reason: str = "Operator confirmed this RCA was started by mistake.",
    idempotency_key: str = "idem_cancel_001",
    trace_id: str = "trc_cancel_001",
) -> CancelRCAWorkflowCommand:
    """构造默认取消请求。"""
    return CancelRCAWorkflowCommand(
        tenant_id=tenant_id,
        workflow_run_id="wfr_001",
        expected_version=expected_version,
        reason=reason,
        idempotency_key=idempotency_key,
        requested_by="admin_001",
        trace_id=trace_id,
    )


def build_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> RCACancellationService:
    """组装真实 UoW 和可控协作者。"""
    return RCACancellationService(
        unit_of_work_factory=lambda: SQLAlchemyUnitOfWork(session_factory),
        identifier_generator=FixedIdentifiers(),
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"trace_id": "trc_cancel\x7fforged"},
        {"reason": "Operator canceled\x7fstale RCA."},
    ],
)
def test_cancel_command_rejects_del_before_service_boundary(
    overrides: dict[str, object],
) -> None:
    """取消请求进入服务前就应拒绝不可见DEL污染。"""
    with pytest.raises(AppValidationError):
        build_command(**overrides)  # type: ignore[arg-type]


async def count_rows(
    session_factory: async_sessionmaker[AsyncSession],
    model: type,
) -> int:
    """统计指定表记录数。"""
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_cancel_is_atomic_redacted_and_replayable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """取消事实和无正文审计事件应原子提交，相同请求可安全重放。"""
    service = build_service(session_factory)
    command = build_command(
        reason=(
            "Operator copied password=hunter2 token=secret-token\nfrom a private note."
        )
    )

    first = await service.cancel(command)
    replay = await service.cancel(
        build_command(
            reason=command.reason,
            trace_id="trc_cancel_retry",
        )
    )

    assert first.status == WorkflowRunStatus.CANCELED.value
    assert first.version == 2
    assert first.cancellation_reason == (
        "Operator copied password=[REDACTED] token=[REDACTED] from a private note."
    )
    assert replay.is_duplicate is True
    assert replay.version == first.version
    assert replay.trace_id == "trc_cancel_retry"
    assert await count_rows(session_factory, OutboxEventRecord) == 1

    async with session_factory() as session:
        workflow_run = await session.get(WorkflowRunRecord, "wfr_001")
        event = await session.get(OutboxEventRecord, "evt_rca_canceled_001")
    assert workflow_run is not None
    assert workflow_run.status == WorkflowRunStatus.CANCELED.value
    assert workflow_run.canceled_by == "admin_001"
    assert workflow_run.cancellation_reason == first.cancellation_reason
    assert workflow_run.canceled_at == NOW
    assert event is not None
    assert event.event_type == "rca.canceled"
    assert event.payload["cancellation_reason_sha256"]
    assert "cancellation_reason" not in event.payload
    assert "hunter2" not in repr(workflow_run)
    assert "secret-token" not in repr(workflow_run)
    assert "hunter2" not in str(event.payload)
    assert "secret-token" not in str(event.payload)


async def test_idempotency_reuse_and_terminal_cancellation_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同键不同请求和终态上的新取消请求都必须冲突。"""
    service = build_service(session_factory)
    await service.cancel(build_command())

    with pytest.raises(ConflictError, match="Idempotency key"):
        await service.cancel(build_command(reason="Different reason."))
    with pytest.raises(ConflictError, match="terminal"):
        await service.cancel(
            build_command(
                expected_version=2,
                idempotency_key="idem_cancel_002",
                reason="Second cancellation.",
            )
        )
    assert await count_rows(session_factory, OutboxEventRecord) == 1


async def test_stale_version_and_cross_tenant_do_not_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """过期 ETag 与跨租户请求都不能留下取消事实或审计事件。"""
    service = build_service(session_factory)

    with pytest.raises(ConflictError, match="version"):
        await service.cancel(build_command(expected_version=2))
    with pytest.raises(ResourceNotFound, match="Workflow run"):
        await service.cancel(build_command(tenant_id="tenant_other"))

    async with session_factory() as session:
        workflow_run = await session.get(WorkflowRunRecord, "wfr_001")
    assert workflow_run is not None
    assert workflow_run.status == WorkflowRunStatus.PENDING.value
    assert workflow_run.canceled_at is None
    assert await count_rows(session_factory, OutboxEventRecord) == 0
