from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.rca import StartRCACommand
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.services.rca_service import (
    RCAApplicationService,
)
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    IncidentStatus,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import ConflictError, ResourceNotFound
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyIncidentRepository,
    SQLAlchemyUnitOfWork,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.incident import (
    IncidentRecord,
)
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)
from devops_agent_platform.infrastructure.database.models.workflow_run import (
    WorkflowRunRecord,
)

NOW = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)


class SequenceIdentifierGenerator:
    """按测试给定顺序生成工作流和事件标识。"""

    def __init__(
        self,
        workflow_run_ids: list[str],
        event_ids: list[str],
    ) -> None:
        self._workflow_run_ids = iter(workflow_run_ids)
        self._event_ids = iter(event_ids)

    def new_alert_id(self) -> str:
        """本组测试不会生成告警标识。"""
        raise AssertionError("RCA用例不应生成告警标识")

    def new_incident_id(self) -> str:
        """本组测试不会生成事故标识。"""
        raise AssertionError("RCA用例不应生成事故标识")

    def new_event_id(self) -> str:
        """返回下一个可预测事件标识。"""
        return next(self._event_ids)

    def new_workflow_run_id(self) -> str:
        """返回下一个可预测工作流运行标识。"""
        return next(self._workflow_run_ids)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建覆盖真实事务和数据库约束的异步测试数据库。"""
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


def build_incident(
    incident_id: str = "inc_rca_001",
    tenant_id: str = "tenant_001",
) -> Incident:
    """构造处于开放状态、允许启动RCA的事故。"""
    return Incident(
        incident_id=incident_id,
        tenant_id=tenant_id,
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.OPEN,
        title="Checkout error rate is above threshold",
        created_at=NOW,
        updated_at=NOW,
    )


def build_command(
    *,
    incident_id: str = "inc_rca_001",
    tenant_id: str = "tenant_001",
    operator_id: str = "operator_001",
    idempotency_key: str = "idem_rca_001",
    trace_id: str = "trc_rca_001",
) -> StartRCACommand:
    """构造启动RCA的应用层命令。"""
    return StartRCACommand(
        incident_id=incident_id,
        tenant_id=tenant_id,
        operator_id=operator_id,
        idempotency_key=idempotency_key,
        trace_id=trace_id,
    )


def build_service(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workflow_run_ids: list[str],
    event_ids: list[str],
) -> RCAApplicationService:
    """使用真实Unit of Work和可预测标识生成器构造服务。"""
    return RCAApplicationService(
        unit_of_work_factory=lambda: SQLAlchemyUnitOfWork(session_factory),
        identifier_generator=SequenceIdentifierGenerator(
            workflow_run_ids=workflow_run_ids,
            event_ids=event_ids,
        ),
        clock=lambda: NOW,
    )


async def save_incident(
    session_factory: async_sessionmaker[AsyncSession],
    incident: Incident,
) -> None:
    """在独立事务中准备测试事故。"""
    async with session_factory() as session:
        await SQLAlchemyIncidentRepository(session).save(incident)
        await session.commit()


async def count_rows(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[IncidentRecord]
    | type[WorkflowRunRecord]
    | type[OutboxEventRecord],
) -> int:
    """通过独立Session统计已提交记录。"""
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(model))
    return int(count or 0)


async def test_start_rca_persists_workflow_incident_and_outbox_atomically(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """首次请求应在同一事务内写入三个一致性对象。"""
    await save_incident(session_factory, build_incident())
    service = build_service(
        session_factory,
        workflow_run_ids=["wfr_001"],
        event_ids=["evt_rca_001"],
    )

    result = await service.start_rca(build_command())

    assert result.workflow_run_id == "wfr_001"
    assert result.status == WorkflowRunStatus.PENDING.value
    assert result.is_duplicate is False

    async with session_factory() as session:
        incident = await session.get(IncidentRecord, "inc_rca_001")
        workflow = await session.get(WorkflowRunRecord, "wfr_001")
        event = await session.get(OutboxEventRecord, "evt_rca_001")

    assert incident is not None
    assert incident.status == IncidentStatus.ANALYZING.value
    assert workflow is not None
    assert workflow.status == WorkflowRunStatus.PENDING.value
    assert workflow.idempotency_key_hash != "idem_rca_001"
    assert event is not None
    assert event.event_type == "rca.requested"
    assert event.payload["workflow_run_id"] == "wfr_001"
    assert event.trace_id == "trc_rca_001"


async def test_same_request_returns_existing_result_without_new_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一幂等键重放不得创建第二条运行记录或事件。"""
    await save_incident(session_factory, build_incident())
    service = build_service(
        session_factory,
        workflow_run_ids=["wfr_001"],
        event_ids=["evt_rca_001"],
    )
    command = build_command()

    first = await service.start_rca(command)
    duplicate = await service.start_rca(command)

    assert duplicate.workflow_run_id == first.workflow_run_id
    assert duplicate.is_duplicate is True
    assert await count_rows(session_factory, WorkflowRunRecord) == 1
    assert await count_rows(session_factory, OutboxEventRecord) == 1


async def test_reusing_idempotency_key_for_another_request_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一个键不能被不同操作人复用，避免错误吞掉新请求。"""
    await save_incident(session_factory, build_incident())
    service = build_service(
        session_factory,
        workflow_run_ids=["wfr_001"],
        event_ids=["evt_rca_001"],
    )
    await service.start_rca(build_command())

    with pytest.raises(ConflictError, match="another RCA request"):
        await service.start_rca(build_command(operator_id="operator_002"))

    assert await count_rows(session_factory, WorkflowRunRecord) == 1
    assert await count_rows(session_factory, OutboxEventRecord) == 1


async def test_incident_allows_only_one_active_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """不同幂等键也不能绕过事故级活动运行唯一约束。"""
    await save_incident(session_factory, build_incident())
    service = build_service(
        session_factory,
        workflow_run_ids=["wfr_001"],
        event_ids=["evt_rca_001"],
    )
    await service.start_rca(build_command())

    with pytest.raises(ConflictError, match="active workflow"):
        await service.start_rca(
            build_command(idempotency_key="idem_rca_002")
        )

    assert await count_rows(session_factory, WorkflowRunRecord) == 1


async def test_cross_tenant_incident_lookup_is_not_exposed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """租户不匹配时按资源不存在处理，避免泄漏其他租户数据。"""
    await save_incident(session_factory, build_incident())
    service = build_service(
        session_factory,
        workflow_run_ids=[],
        event_ids=[],
    )

    with pytest.raises(ResourceNotFound, match="Incident not found"):
        await service.start_rca(build_command(tenant_id="tenant_002"))

    assert await count_rows(session_factory, WorkflowRunRecord) == 0


async def test_outbox_conflict_rolls_back_incident_and_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """事件落库冲突时事故状态和工作流记录必须一起回滚。"""
    await save_incident(session_factory, build_incident())
    duplicate_event = OutboxEvent(
        event_id="evt_rca_duplicate",
        tenant_id="tenant_001",
        aggregate_type="WorkflowRun",
        aggregate_id="old_wfr",
        event_type="rca.requested",
        schema_version=1,
        payload={"workflow_run_id": "old_wfr"},
        occurred_at=NOW,
        trace_id="trc_old",
    )
    async with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        await unit_of_work.outbox.add(duplicate_event)
        await unit_of_work.commit()

    service = build_service(
        session_factory,
        workflow_run_ids=["wfr_rollback"],
        event_ids=["evt_rca_duplicate"],
    )

    with pytest.raises(ConflictError):
        await service.start_rca(build_command())

    async with session_factory() as session:
        incident = await session.get(IncidentRecord, "inc_rca_001")
        workflow = await session.get(WorkflowRunRecord, "wfr_rollback")

    assert incident is not None
    assert incident.status == IncidentStatus.OPEN.value
    assert workflow is None
    assert await count_rows(session_factory, OutboxEventRecord) == 1


class CommitRaceState:
    """保存并发提交测试中第一个事务尝试写入的运行对象。"""

    def __init__(self) -> None:
        self.factory_calls = 0
        self.saved_workflow: WorkflowRun | None = None


class RaceWorkflowRunRepository:
    """模拟提交前查不到、提交冲突后能读到赢家记录的仓储。"""

    def __init__(self, state: CommitRaceState, recovery: bool) -> None:
        self._state = state
        self._recovery = recovery

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> WorkflowRun | None:
        """恢复事务返回另一并发请求已经提交的相同记录。"""
        if not self._recovery:
            return None
        return self._state.saved_workflow

    async def get_active_by_incident(
        self,
        tenant_id: str,
        incident_id: str,
    ) -> WorkflowRun | None:
        """首次事务模拟在快照中尚未看到活动运行。"""
        return None

    async def save(self, workflow_run: WorkflowRun) -> None:
        """记录首次事务准备写入的对象，作为并发赢家的等价快照。"""
        self._state.saved_workflow = workflow_run


class RaceIncidentRepository:
    """为提交竞态测试提供最小事故仓储行为。"""

    async def get_by_id(
        self,
        incident_id: str,
        tenant_id: str,
    ) -> Incident | None:
        """返回允许进入分析状态的事故。"""
        return build_incident(incident_id=incident_id, tenant_id=tenant_id)

    async def save(self, incident: Incident) -> None:
        """本测试只关注提交竞态，不模拟持久化。"""


class RaceOutboxRepository:
    """为提交竞态测试提供最小Outbox行为。"""

    async def add(self, event: OutboxEvent) -> None:
        """本测试只验证事件已进入同一事务调用链。"""


class CommitRaceUnitOfWork:
    """第一次提交抛冲突，第二次上下文仅用于只读恢复。"""

    def __init__(self, state: CommitRaceState, recovery: bool) -> None:
        self._state = state
        self._recovery = recovery
        self.workflow_runs = RaceWorkflowRunRepository(state, recovery)
        self.incidents = RaceIncidentRepository()
        self.outbox = RaceOutboxRepository()

    async def __aenter__(self) -> "CommitRaceUnitOfWork":
        """进入可控事务上下文。"""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool:
        """不吞掉应用异常。"""
        return False

    async def commit(self) -> None:
        """首次提交模拟数据库唯一约束竞争失败。"""
        if not self._recovery:
            raise ConflictError("simulated concurrent commit conflict")

    async def rollback(self) -> None:
        """满足Unit of Work端口，本测试不会主动调用。"""


def build_commit_race_unit_of_work(
    state: CommitRaceState,
) -> CommitRaceUnitOfWork:
    """依次创建写事务和只读恢复事务。"""
    recovery = state.factory_calls > 0
    state.factory_calls += 1
    return CommitRaceUnitOfWork(state, recovery)


async def test_commit_conflict_recovers_by_one_read_only_lookup() -> None:
    """唯一约束竞态后应读取赢家结果，不得重放整个写事务。"""
    state = CommitRaceState()
    service = RCAApplicationService(
        unit_of_work_factory=lambda: build_commit_race_unit_of_work(state),
        identifier_generator=SequenceIdentifierGenerator(
            workflow_run_ids=["wfr_race_001"],
            event_ids=["evt_race_001"],
        ),
        clock=lambda: NOW,
    )

    result = await service.start_rca(build_command())

    assert result.workflow_run_id == "wfr_race_001"
    assert result.is_duplicate is True
    assert state.factory_calls == 2
