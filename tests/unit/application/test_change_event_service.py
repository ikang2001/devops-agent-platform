import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.change_events import (
    ReceiveChangeEventCommand,
)
from devops_agent_platform.application.services.change_event_service import (
    ChangeEventApplicationService,
)
from devops_agent_platform.domain.enums import ChangeEventStatus, ChangeType
from devops_agent_platform.domain.exceptions import AppValidationError, ConflictError
from devops_agent_platform.domain.models.change_event import ChangeEvent
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyChangeEventRepository,
    SQLAlchemyUnitOfWork,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.change_event import (
    ChangeEventRecord,
)
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)

NOW = datetime(2026, 8, 17, 14, 5, tzinfo=UTC)


class SequenceIdentifierGenerator:
    """按测试给定顺序生成 Change 和 Outbox ID。"""

    def __init__(self, change_event_ids: list[str], event_ids: list[str]) -> None:
        self._change_event_ids = iter(change_event_ids)
        self._event_ids = iter(event_ids)

    def new_change_event_id(self) -> str:
        return next(self._change_event_ids)

    def new_event_id(self) -> str:
        return next(self._event_ids)


class ConflictThenExistingChangeRepository:
    """模拟并发赢家先提交、当前写入收到唯一约束冲突。"""

    def __init__(self) -> None:
        self.existing: ChangeEvent | None = None

    async def get_by_external_event_id(
        self,
        tenant_id: str,
        source: str,
        external_event_id: str,
    ) -> ChangeEvent | None:
        return self.existing

    async def save(self, change_event: ChangeEvent) -> None:
        self.existing = replace(change_event, change_event_id="chg_winner")
        raise ConflictError("simulated concurrent unique conflict")


class RecordingOutboxRepository:
    def __init__(self) -> None:
        self.add_count = 0

    async def add(self, event: object) -> None:
        self.add_count += 1


class ConflictRecoveryUnitOfWork:
    """只实现并发幂等恢复测试所需的 UoW 表面。"""

    def __init__(self) -> None:
        self.change_events = ConflictThenExistingChangeRepository()
        self.outbox = RecordingOutboxRepository()

    async def __aenter__(self) -> "ConflictRecoveryUnitOfWork":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def commit(self) -> None:
        raise AssertionError("conflicting write must not commit")


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def build_command(
    *,
    version_after: str = "v2",
    trace_id: str = "trc_change_001",
    metadata: dict[str, object] | None = None,
) -> ReceiveChangeEventCommand:
    started_at = datetime(2026, 8, 17, 14, 2, tzinfo=UTC)
    return ReceiveChangeEventCommand(
        tenant_id="tenant_001",
        source="argocd",
        external_event_id="deploy_payment_v2",
        service_name="payment-service",
        resource_type="deployment",
        resource_id="payment-service",
        change_type=ChangeType.DEPLOYMENT,
        status=ChangeEventStatus.SUCCEEDED,
        version_before="v1",
        version_after=version_after,
        operator_id="deployment-bot",
        summary="payment token=summary-secret\nupgraded from v1 to v2",
        metadata=metadata
        or {
            "cluster": "minishop",
            "api_token": "metadata-secret",
            "detail": "owner=ops@example.com",
        },
        started_at=started_at,
        completed_at=started_at + timedelta(minutes=1),
        trace_id=trace_id,
    )


def build_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> ChangeEventApplicationService:
    return ChangeEventApplicationService(
        unit_of_work_factory=lambda: SQLAlchemyUnitOfWork(session_factory),
        identifier_generator=SequenceIdentifierGenerator(
            change_event_ids=["chg_001"],
            event_ids=["evt_change_001"],
        ),
        clock=lambda: NOW,
    )


async def count_rows(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[ChangeEventRecord] | type[OutboxEventRecord],
) -> int:
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(model))
    return int(count or 0)


async def test_receive_change_event_sanitizes_and_commits_outbox_atomically(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    result = await build_service(session_factory).receive_change_event(build_command())

    assert result.change_event_id == "chg_001"
    assert result.status == "ACCEPTED"
    assert result.is_duplicate is False

    async with session_factory() as session:
        event = await SQLAlchemyChangeEventRepository(session).get_by_id(
            "chg_001",
            "tenant_001",
        )
        outbox = await session.get(OutboxEventRecord, "evt_change_001")

    assert event is not None
    assert event.created_at == NOW
    assert event.summary == ("payment token=[REDACTED] upgraded from v1 to v2")
    assert json.loads(event.metadata_json) == {
        "api_token": "[REDACTED]",
        "cluster": "minishop",
        "detail": "owner=[REDACTED_EMAIL]",
    }
    assert outbox is not None
    assert outbox.event_type == "change_event.received"
    assert outbox.aggregate_id == "chg_001"
    assert outbox.payload["service_name"] == "payment-service"
    persisted = f"{event!r}{outbox.payload!r}"
    assert "summary-secret" not in persisted
    assert "metadata-secret" not in persisted
    assert "ops@example.com" not in persisted


async def test_repeated_change_event_returns_original_id_without_new_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = build_service(session_factory)
    first = await service.receive_change_event(build_command())
    duplicate = await service.receive_change_event(
        build_command(trace_id="trc_change_retry")
    )

    assert duplicate.change_event_id == first.change_event_id
    assert duplicate.status == "DUPLICATE"
    assert duplicate.trace_id == "trc_change_retry"
    assert duplicate.is_duplicate is True
    assert await count_rows(session_factory, ChangeEventRecord) == 1
    assert await count_rows(session_factory, OutboxEventRecord) == 1


async def test_same_idempotency_key_with_different_payload_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = build_service(session_factory)
    await service.receive_change_event(build_command())

    with pytest.raises(ConflictError, match="idempotency key reuse"):
        await service.receive_change_event(build_command(version_after="v3"))

    assert await count_rows(session_factory, ChangeEventRecord) == 1
    assert await count_rows(session_factory, OutboxEventRecord) == 1


async def test_concurrent_unique_conflict_recovers_with_one_read_only_lookup() -> None:
    unit_of_work = ConflictRecoveryUnitOfWork()
    service = ChangeEventApplicationService(
        unit_of_work_factory=lambda: unit_of_work,  # type: ignore[arg-type]
        identifier_generator=SequenceIdentifierGenerator(
            change_event_ids=["chg_loser"],
            event_ids=[],
        ),
        clock=lambda: NOW,
    )

    result = await service.receive_change_event(build_command())

    assert result.change_event_id == "chg_winner"
    assert result.status == "DUPLICATE"
    assert result.is_duplicate is True
    assert unit_of_work.outbox.add_count == 0


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("tenant_id", "tenant 001"),
        ("source", "argocd\nforged"),
        ("change_type", "DEPLOYMENT"),
        ("status", "SUCCEEDED"),
        ("started_at", datetime(2026, 8, 17, 14, 2)),
        ("trace_id", ""),
    ],
)
def test_receive_change_command_rejects_invalid_internal_input(
    field_name: str,
    field_value: object,
) -> None:
    values = build_command().__dict__.copy()
    values[field_name] = field_value

    with pytest.raises(AppValidationError):
        ReceiveChangeEventCommand(**values)  # type: ignore[arg-type]
