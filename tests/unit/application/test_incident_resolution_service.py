from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.incidents import (
    CloseIncidentCommand,
    ResolveIncidentCommand,
)
from devops_agent_platform.application.services.incident_resolution_service import (
    IncidentResolutionService,
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
    SQLAlchemyUnitOfWork,
    SQLAlchemyWorkflowRunRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)
from devops_agent_platform.infrastructure.database.models.incident import (
    IncidentRecord,
)
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)

NOW = datetime(2026, 7, 2, 9, 0, tzinfo=UTC)


class FixedIdentifiers:
    """为解决审计事件返回固定 ID。"""

    def __init__(
        self,
        event_ids: list[str] | None = None,
    ) -> None:
        self._event_ids = iter(event_ids or ["evt_resolution_001", "evt_closure_001"])

    def new_event_id(self) -> str:
        return next(self._event_ids)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建包含一个活动事故的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(IncidentMapper.to_record(build_incident()))
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


def build_incident() -> Incident:
    """构造可被人工解决的分析中事故。"""
    return Incident(
        incident_id="inc_001",
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.ANALYZING,
        title="Checkout outage",
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(minutes=5),
    )


def build_workflow_run(
    *,
    workflow_run_id: str = "wfr_resolution_001",
) -> WorkflowRun:
    """构造同一事故下尚未执行完成的 RCA 工作流。"""
    return WorkflowRun(
        workflow_run_id=workflow_run_id,
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="admin_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_rca_001",
        status=WorkflowRunStatus.PENDING,
        created_at=NOW - timedelta(minutes=4),
        updated_at=NOW - timedelta(minutes=4),
        started_at=None,
        ended_at=None,
        step_count=0,
    )


def build_command(
    *,
    tenant_id: str = "tenant_001",
    expected_version: int = 1,
    reason: str = "Mitigation verified.",
    idempotency_key: str = "idem_resolution_001",
    trace_id: str = "trc_resolution_001",
) -> ResolveIncidentCommand:
    """构造默认解决请求。"""
    return ResolveIncidentCommand(
        tenant_id=tenant_id,
        incident_id="inc_001",
        expected_version=expected_version,
        reason=reason,
        idempotency_key=idempotency_key,
        requested_by="admin_001",
        trace_id=trace_id,
    )


def build_close_command(
    *,
    tenant_id: str = "tenant_001",
    expected_version: int = 2,
    reason: str = "Post-incident checklist completed.",
    idempotency_key: str = "idem_closure_001",
    trace_id: str = "trc_closure_001",
) -> CloseIncidentCommand:
    """构造默认关闭请求。"""
    return CloseIncidentCommand(
        tenant_id=tenant_id,
        incident_id="inc_001",
        expected_version=expected_version,
        reason=reason,
        idempotency_key=idempotency_key,
        requested_by="admin_001",
        trace_id=trace_id,
    )


def build_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> IncidentResolutionService:
    """组装真实 UoW 和可控协作者。"""
    return IncidentResolutionService(
        unit_of_work_factory=lambda: SQLAlchemyUnitOfWork(session_factory),
        identifier_generator=FixedIdentifiers(),
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("factory_name", "overrides"),
    [
        ("resolve", {"trace_id": "trc_resolution\x7fforged"}),
        ("resolve", {"reason": "Mitigation verified.\x7fforged"}),
        ("close", {"trace_id": "trc_closure\x7fforged"}),
        ("close", {"reason": "Post-incident checklist\x7fforged"}),
    ],
)
def test_incident_resolution_commands_reject_del_control_character(
    factory_name: str,
    overrides: dict[str, object],
) -> None:
    """人工终态命令不能把DEL带入幂等、审计或原因字段。"""
    factory = build_command if factory_name == "resolve" else build_close_command

    with pytest.raises(AppValidationError):
        factory(**overrides)  # type: ignore[arg-type]


async def count_rows(
    session_factory: async_sessionmaker[AsyncSession],
    model: type,
) -> int:
    """统计指定表记录数。"""
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def seed_active_workflow(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workflow_run_id: str = "wfr_resolution_001",
) -> None:
    """向当前事故写入一个仍活跃的 RCA 工作流。"""
    async with session_factory() as session:
        await SQLAlchemyWorkflowRunRepository(session).save(
            build_workflow_run(workflow_run_id=workflow_run_id)
        )
        await session.commit()


async def test_resolve_is_atomic_redacted_and_replayable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """解决事实和无正文审计事件应原子提交，相同请求可安全重放。"""
    service = build_service(session_factory)
    command = build_command(
        reason=("Verified password=hunter2 token=secret-token\nafter rollback.")
    )

    first = await service.resolve(command)
    replay = await service.resolve(
        build_command(
            reason=command.reason,
            trace_id="trc_resolution_retry",
        )
    )

    assert first.status == IncidentStatus.RESOLVED.value
    assert first.version == 2
    assert first.resolution_reason == (
        "Verified password=[REDACTED] token=[REDACTED] after rollback."
    )
    assert replay.is_duplicate is True
    assert replay.version == first.version
    assert replay.trace_id == "trc_resolution_retry"
    assert await count_rows(session_factory, OutboxEventRecord) == 1

    async with session_factory() as session:
        incident = await session.get(IncidentRecord, "inc_001")
        event = await session.get(
            OutboxEventRecord,
            "evt_resolution_001",
        )
    assert incident is not None
    assert incident.status == IncidentStatus.RESOLVED.value
    assert incident.resolved_by == "admin_001"
    assert incident.resolution_reason == first.resolution_reason
    assert incident.resolved_at == NOW
    assert event is not None
    assert event.event_type == "incident.resolved"
    assert event.payload["resolution_reason_sha256"]
    assert "resolution_reason" not in event.payload
    assert "hunter2" not in repr(incident)
    assert "secret-token" not in repr(incident)
    assert "hunter2" not in str(event.payload)
    assert "secret-token" not in str(event.payload)


async def test_idempotency_reuse_and_second_resolution_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同键不同请求和终态上的新解决请求都必须冲突。"""
    service = build_service(session_factory)
    await service.resolve(build_command())

    with pytest.raises(ConflictError, match="Idempotency key"):
        await service.resolve(build_command(reason="Different resolution."))
    with pytest.raises(ConflictError, match="active"):
        await service.resolve(
            build_command(
                expected_version=2,
                idempotency_key="idem_resolution_002",
                reason="Second resolution.",
            )
        )
    assert await count_rows(session_factory, OutboxEventRecord) == 1


async def test_stale_version_and_cross_tenant_do_not_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """过期 ETag 与跨租户请求都不能留下解决事实或审计事件。"""
    service = build_service(session_factory)

    with pytest.raises(ConflictError, match="version"):
        await service.resolve(build_command(expected_version=2))
    with pytest.raises(ResourceNotFound, match="Incident"):
        await service.resolve(build_command(tenant_id="tenant_other"))

    async with session_factory() as session:
        incident = await session.get(IncidentRecord, "inc_001")
    assert incident is not None
    assert incident.status == IncidentStatus.ANALYZING.value
    assert incident.resolved_at is None
    assert await count_rows(session_factory, OutboxEventRecord) == 0


async def test_resolve_rejects_active_rca_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """RCA仍在排队或运行时，人工解决不能抢先写终态。"""
    await seed_active_workflow(session_factory)
    service = build_service(session_factory)

    with pytest.raises(ConflictError, match="active RCA workflow"):
        await service.resolve(build_command())

    async with session_factory() as session:
        incident = await session.get(IncidentRecord, "inc_001")
    assert incident is not None
    assert incident.status == IncidentStatus.ANALYZING.value
    assert incident.resolved_at is None
    assert await count_rows(session_factory, OutboxEventRecord) == 0


async def test_close_is_atomic_redacted_and_replayable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """关闭事实和无正文审计事件应原子提交，相同请求可安全重放。"""
    service = build_service(session_factory)
    await service.resolve(build_command())
    command = build_close_command(
        reason=("Postmortem password=hunter2 token=secret-token\napproved.")
    )

    first = await service.close(command)
    replay = await service.close(
        build_close_command(
            reason=command.reason,
            trace_id="trc_closure_retry",
        )
    )

    assert first.status == IncidentStatus.CLOSED.value
    assert first.version == 3
    assert first.closure_reason == (
        "Postmortem password=[REDACTED] token=[REDACTED] approved."
    )
    assert replay.is_duplicate is True
    assert replay.version == first.version
    assert replay.trace_id == "trc_closure_retry"
    assert await count_rows(session_factory, OutboxEventRecord) == 2

    async with session_factory() as session:
        incident = await session.get(IncidentRecord, "inc_001")
        event = await session.get(OutboxEventRecord, "evt_closure_001")
    assert incident is not None
    assert incident.status == IncidentStatus.CLOSED.value
    assert incident.closed_by == "admin_001"
    assert incident.closure_reason == first.closure_reason
    assert incident.closed_at == NOW
    assert event is not None
    assert event.event_type == "incident.closed"
    assert event.payload["closure_reason_sha256"]
    assert "closure_reason" not in event.payload
    assert "hunter2" not in repr(incident)
    assert "secret-token" not in repr(incident)
    assert "hunter2" not in str(event.payload)
    assert "secret-token" not in str(event.payload)


async def test_close_rejects_active_rca_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史或异常状态下若仍有活动RCA，关闭必须失败关闭。"""
    service = build_service(session_factory)
    await service.resolve(build_command())
    await seed_active_workflow(
        session_factory,
        workflow_run_id="wfr_closure_001",
    )

    with pytest.raises(ConflictError, match="active RCA workflow"):
        await service.close(build_close_command())

    async with session_factory() as session:
        incident = await session.get(IncidentRecord, "inc_001")
    assert incident is not None
    assert incident.status == IncidentStatus.RESOLVED.value
    assert incident.closed_at is None
    assert await count_rows(session_factory, OutboxEventRecord) == 1


async def test_close_requires_resolved_incident(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """活动事故不能绕过解决确认直接进入关闭状态。"""
    service = build_service(session_factory)

    with pytest.raises(ConflictError, match="resolved"):
        await service.close(build_close_command(expected_version=1))

    async with session_factory() as session:
        incident = await session.get(IncidentRecord, "inc_001")
    assert incident is not None
    assert incident.status == IncidentStatus.ANALYZING.value
    assert incident.closed_at is None
    assert await count_rows(session_factory, OutboxEventRecord) == 0


async def test_close_idempotency_reuse_and_second_closure_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同键不同关闭请求和终态上的新关闭请求都必须冲突。"""
    service = build_service(session_factory)
    await service.resolve(build_command())
    await service.close(build_close_command())

    with pytest.raises(ConflictError, match="Idempotency key"):
        await service.close(build_close_command(reason="Different closure."))
    with pytest.raises(ConflictError, match="resolved"):
        await service.close(
            build_close_command(
                expected_version=3,
                idempotency_key="idem_closure_002",
                reason="Second closure.",
            )
        )
    assert await count_rows(session_factory, OutboxEventRecord) == 2
