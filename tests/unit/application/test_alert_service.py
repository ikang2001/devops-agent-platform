from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.application.services.alert_service import (
    AlertApplicationService,
)
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    IncidentCreationAction,
)
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.domain.policies.incident_creation import (
    IncidentCreationPolicy,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyAlertRepository,
    SQLAlchemyIncidentRepository,
    SQLAlchemyUnitOfWork,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.alert import AlertRecord
from devops_agent_platform.infrastructure.database.models.incident import IncidentRecord
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)


class SequenceIdentifierGenerator:
    """按测试给定顺序生成业务 ID。"""

    def __init__(
        self,
        alert_ids: list[str],
        incident_ids: list[str],
        event_ids: list[str],
    ) -> None:
        self._alert_ids = iter(alert_ids)
        self._incident_ids = iter(incident_ids)
        self._event_ids = iter(event_ids)

    def new_alert_id(self) -> str:
        return next(self._alert_ids)

    def new_incident_id(self) -> str:
        return next(self._incident_ids)

    def new_event_id(self) -> str:
        return next(self._event_ids)


class RecordingCorrelationLock:
    """记录应用服务申请的租户和服务锁。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def acquire(self, tenant_id: str, service_name: str) -> None:
        self.calls.append((tenant_id, service_name))


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建应用用例测试使用的真实异步数据库会话工厂。"""
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
    external_event_id: str = "evt_001",
    severity: AlertSeverity = AlertSeverity.WARNING,
    starts_at: datetime | None = None,
    service_name: str = "checkout-api",
    summary: str = "Checkout error rate is above threshold",
) -> ReceiveAlertCommand:
    """构造告警接入命令。"""
    return ReceiveAlertCommand(
        tenant_id="tenant_001",
        source="alertmanager",
        service_name=service_name,
        severity=severity,
        summary=summary,
        starts_at=starts_at or datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        fingerprint="fp_checkout_error_rate",
        external_event_id=external_event_id,
        trace_id="trc_001",
    )


def build_service(
    session_factory: async_sessionmaker[AsyncSession],
    alert_ids: list[str],
    incident_ids: list[str],
    event_ids: list[str],
    correlation_lock: RecordingCorrelationLock | None = None,
) -> AlertApplicationService:
    """使用真实 Unit of Work 和可预测 ID 构造应用服务。"""
    lock = correlation_lock or RecordingCorrelationLock()
    return AlertApplicationService(
        unit_of_work_factory=lambda: SQLAlchemyUnitOfWork(
            session_factory,
            correlation_lock_factory=lambda session: lock,
        ),
        incident_policy=IncidentCreationPolicy(),
        identifier_generator=SequenceIdentifierGenerator(
            alert_ids=alert_ids,
            incident_ids=incident_ids,
            event_ids=event_ids,
        ),
    )


async def count_rows(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[AlertRecord] | type[IncidentRecord] | type[OutboxEventRecord],
) -> int:
    """通过独立 Session 统计已提交记录。"""
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(model))
    return int(count or 0)


async def test_warning_alert_creates_incident_and_commits_link_atomically(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    correlation_lock = RecordingCorrelationLock()
    service = build_service(
        session_factory,
        alert_ids=["alt_001"],
        incident_ids=["inc_001"],
        event_ids=["evt_outbox_001"],
        correlation_lock=correlation_lock,
    )

    result = await service.receive_alert(build_command())

    assert result.status == "ACCEPTED"
    assert result.incident_action is IncidentCreationAction.CREATE
    assert result.alert_id == "alt_001"
    assert result.incident_id == "inc_001"
    assert correlation_lock.calls == [("tenant_001", "checkout-api")]

    async with session_factory() as session:
        alert = await SQLAlchemyAlertRepository(
            session
        ).get_by_external_event_id(
            "tenant_001",
            "alertmanager",
            "evt_001",
        )
        incident = await SQLAlchemyIncidentRepository(session).get_by_id(
            "inc_001",
            "tenant_001",
        )

    assert alert is not None
    assert alert.incident_id == "inc_001"
    assert incident is not None

    async with session_factory() as session:
        outbox = await session.get(OutboxEventRecord, "evt_outbox_001")

    assert outbox is not None
    assert outbox.status == "PENDING"
    assert outbox.attempts == 0
    assert outbox.event_type == "incident.created"
    assert outbox.schema_version == 1
    assert outbox.aggregate_id == "inc_001"
    assert outbox.trace_id == "trc_001"
    assert outbox.payload["alert_id"] == "alt_001"


async def test_alert_summary_is_redacted_before_storage_and_incident_title(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """外部告警摘要进入 Alert 和 Incident 前必须脱敏并压成单行。"""
    service = build_service(
        session_factory,
        alert_ids=["alt_secret_001"],
        incident_ids=["inc_secret_001"],
        event_ids=["evt_outbox_secret_001"],
    )

    result = await service.receive_alert(
        build_command(
            summary=(
                "Checkout password=hunter2 token=secret-token\n"
                "needs triage"
            )
        )
    )

    assert result.incident_action is IncidentCreationAction.CREATE
    async with session_factory() as session:
        alert_record = await session.get(AlertRecord, "alt_secret_001")
        incident_record = await session.get(
            IncidentRecord,
            "inc_secret_001",
        )
        outbox = await session.get(
            OutboxEventRecord,
            "evt_outbox_secret_001",
        )

    expected = (
        "Checkout password=[REDACTED] token=[REDACTED] needs triage"
    )
    assert alert_record is not None
    assert alert_record.summary == expected
    assert incident_record is not None
    assert incident_record.title == expected
    assert outbox is not None
    assert "hunter2" not in str(outbox.payload)
    assert "secret-token" not in str(outbox.payload)
    assert "hunter2" not in repr(alert_record)
    assert "secret-token" not in repr(alert_record)
    assert "hunter2" not in repr(incident_record)
    assert "secret-token" not in repr(incident_record)


async def test_info_alert_is_saved_without_creating_incident(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    correlation_lock = RecordingCorrelationLock()
    service = build_service(
        session_factory,
        alert_ids=["alt_info_001"],
        incident_ids=[],
        event_ids=[],
        correlation_lock=correlation_lock,
    )

    result = await service.receive_alert(
        build_command(severity=AlertSeverity.INFO)
    )

    assert result.incident_action is IncidentCreationAction.IGNORE
    assert result.incident_id is None
    assert await count_rows(session_factory, AlertRecord) == 1
    assert await count_rows(session_factory, IncidentRecord) == 0
    assert await count_rows(session_factory, OutboxEventRecord) == 0
    assert correlation_lock.calls == []


async def test_new_event_attaches_to_active_incident_and_escalates_severity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = build_service(
        session_factory,
        alert_ids=["alt_001", "alt_002"],
        incident_ids=["inc_001"],
        event_ids=["evt_outbox_001"],
    )
    first_time = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)

    first = await service.receive_alert(
        build_command(starts_at=first_time)
    )
    second = await service.receive_alert(
        build_command(
            external_event_id="evt_002",
            severity=AlertSeverity.CRITICAL,
            starts_at=first_time + timedelta(minutes=5),
        )
    )

    assert first.incident_action is IncidentCreationAction.CREATE
    assert second.incident_action is IncidentCreationAction.ATTACH
    assert second.incident_id == first.incident_id
    assert await count_rows(session_factory, AlertRecord) == 2
    assert await count_rows(session_factory, IncidentRecord) == 1
    assert await count_rows(session_factory, OutboxEventRecord) == 1

    async with session_factory() as session:
        incident = await SQLAlchemyIncidentRepository(session).get_by_id(
            "inc_001",
            "tenant_001",
        )

    assert incident is not None
    assert incident.severity is AlertSeverity.CRITICAL
    assert incident.updated_at == first_time + timedelta(minutes=5)
    assert incident.version == 2


async def test_repeated_event_returns_original_result_without_new_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    correlation_lock = RecordingCorrelationLock()
    service = build_service(
        session_factory,
        alert_ids=["alt_001"],
        incident_ids=["inc_001"],
        event_ids=["evt_outbox_001"],
        correlation_lock=correlation_lock,
    )
    command = build_command()

    first = await service.receive_alert(command)
    duplicate = await service.receive_alert(command)

    assert duplicate.status == "DUPLICATE"
    assert duplicate.is_duplicate is True
    assert duplicate.alert_id == first.alert_id
    assert duplicate.incident_id == first.incident_id
    assert await count_rows(session_factory, AlertRecord) == 1
    assert await count_rows(session_factory, IncidentRecord) == 1
    assert await count_rows(session_factory, OutboxEventRecord) == 1
    assert correlation_lock.calls == [("tenant_001", "checkout-api")]


async def test_alert_conflict_rolls_back_incident_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = build_service(
        session_factory,
        alert_ids=["alt_fixed", "alt_fixed"],
        incident_ids=["inc_001", "inc_002"],
        event_ids=["evt_outbox_001", "evt_outbox_002"],
    )
    first_time = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)
    await service.receive_alert(build_command(starts_at=first_time))

    with pytest.raises(ConflictError):
        await service.receive_alert(
            build_command(
                external_event_id="evt_002",
                severity=AlertSeverity.CRITICAL,
                starts_at=first_time + timedelta(minutes=5),
                service_name="payments-api",
            )
        )

    async with session_factory() as session:
        incident = await SQLAlchemyIncidentRepository(session).get_by_id(
            "inc_001",
            "tenant_001",
        )

    assert incident is not None
    assert incident.severity is AlertSeverity.WARNING
    assert incident.updated_at == first_time
    assert incident.version == 1
    assert await count_rows(session_factory, AlertRecord) == 1
    assert await count_rows(session_factory, IncidentRecord) == 1
    assert await count_rows(session_factory, OutboxEventRecord) == 1

    async with session_factory() as session:
        rolled_back_incident = await session.get(IncidentRecord, "inc_002")
        rolled_back_event = await session.get(
            OutboxEventRecord,
            "evt_outbox_002",
        )

    assert rolled_back_incident is None
    assert rolled_back_event is None
