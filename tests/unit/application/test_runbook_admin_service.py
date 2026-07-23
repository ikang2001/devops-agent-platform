import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.runbooks import (
    PublishRunbookCommand,
    SaveRunbookDraftCommand,
)
from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.application.services.runbook_admin_service import (
    RunbookAdminService,
)
from devops_agent_platform.domain.enums import RunbookStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyRunbookAdminStore,
    SQLAlchemyRunbookSearch,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)
from devops_agent_platform.infrastructure.database.models.runbook import (
    RunbookHeadRecord,
    RunbookOperationRecord,
    RunbookRecord,
)

NOW = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


class DeterministicIdentifiers:
    """为 Runbook 管理测试生成可预测且不重复的标识。"""

    def __init__(self, fixed_event_id: str | None = None) -> None:
        self._runbook_sequence = 0
        self._operation_sequence = 0
        self._event_sequence = 0
        self._fixed_event_id = fixed_event_id

    def new_runbook_id(self) -> str:
        self._runbook_sequence += 1
        return f"rbk_{self._runbook_sequence:03d}"

    def new_runbook_operation_id(self) -> str:
        self._operation_sequence += 1
        return f"rop_{self._operation_sequence:03d}"

    def new_event_id(self) -> str:
        if self._fixed_event_id is not None:
            return self._fixed_event_id
        self._event_sequence += 1
        return f"evt_{self._event_sequence:03d}"


class MutableClock:
    """允许测试显式推进业务时间。"""

    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建 Runbook 管理测试使用的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def build_service(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identifiers: DeterministicIdentifiers | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RunbookAdminService:
    """组装真实事务 Store 和可控协作者。"""
    return RunbookAdminService(
        store=SQLAlchemyRunbookAdminStore(session_factory),
        identifier_generator=identifiers or DeterministicIdentifiers(),
        clock=clock or MutableClock(),
    )


def draft_command(
    *,
    version: str = "v1",
    expected_revision: int = 0,
    idempotency_key: str = "idem_draft_v1",
    title: str = "Checkout incident response",
    summary: str = "Inspect dependencies before taking remediation action.",
    steps: tuple[str, ...] = (
        "Check metrics, logs, and traces.",
        "Escalate to the service owner before rollback.",
    ),
    tenant_id: str = "tenant_001",
    trace_id: str = "trc_draft",
) -> SaveRunbookDraftCommand:
    """构造创建或更新草稿命令。"""
    return SaveRunbookDraftCommand(
        tenant_id=tenant_id,
        runbook_key="checkout.error-rate",
        version=version,
        service_name="checkout-api",
        title=title,
        summary=summary,
        priority=100,
        steps=steps,
        tags=("checkout", "http-5xx"),
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        requested_by="admin_001",
        trace_id=trace_id,
    )


def publish_command(
    *,
    version: str = "v1",
    expected_revision: int = 1,
    idempotency_key: str = "idem_publish_v1",
    tenant_id: str = "tenant_001",
    trace_id: str = "trc_publish",
) -> PublishRunbookCommand:
    """构造发布指定业务版本的命令。"""
    return PublishRunbookCommand(
        tenant_id=tenant_id,
        runbook_key="checkout.error-rate",
        version=version,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        requested_by="admin_001",
        trace_id=trace_id,
    )


async def count_records(
    session_factory: async_sessionmaker[AsyncSession],
    model: type,
) -> int:
    """统计指定 ORM 表的记录数。"""
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_create_draft_is_atomic_and_audit_omits_content(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """首次保存应原子写入草稿、幂等结果和不含正文的审计事件。"""
    result = await build_service(session_factory).save_draft(draft_command())

    assert result.status == RunbookStatus.DRAFT.value
    assert result.revision == 1
    assert result.is_duplicate is False
    assert await count_records(session_factory, RunbookRecord) == 1
    assert (
        await count_records(
            session_factory,
            RunbookOperationRecord,
        )
        == 1
    )
    assert (
        await count_records(
            session_factory,
            OutboxEventRecord,
        )
        == 1
    )
    async with session_factory() as session:
        head = await session.scalar(select(RunbookHeadRecord))
    assert head is not None
    assert head.revision == 1
    assert (
        await count_records(
            session_factory,
            RunbookHeadRecord,
        )
        == 1
    )

    async with session_factory() as session:
        event = await session.scalar(select(OutboxEventRecord))
    assert event is not None
    assert event.event_type == "runbook.draft.saved"
    assert event.payload["content_sha256"]
    assert "steps" not in event.payload
    assert "summary" not in event.payload


async def test_save_draft_redacts_sensitive_content_before_storage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Runbook 正文进入业务表和幂等指纹前必须先脱敏。"""
    result = await build_service(session_factory).save_draft(
        draft_command(
            title="Checkout password=hunter2 response",
            summary="Use token=secret-token only in the vault.",
            steps=(
                "Rotate api_key=private-key before publish.",
                "Notify owner@example.com after mitigation.",
            ),
        )
    )

    assert result.status == RunbookStatus.DRAFT.value
    async with session_factory() as session:
        record = await session.scalar(select(RunbookRecord))
        event = await session.scalar(select(OutboxEventRecord))

    assert record is not None
    assert record.title == "Checkout password=[REDACTED] response"
    assert record.summary == "Use token=[REDACTED] only in the vault."
    assert tuple(json.loads(record.steps_json)) == (
        "Rotate api_key=[REDACTED] before publish.",
        "Notify [REDACTED_EMAIL] after mitigation.",
    )
    assert event is not None
    assert "hunter2" not in str(event.payload)
    assert "secret-token" not in str(event.payload)
    assert "private-key" not in str(event.payload)
    assert "owner@example.com" not in str(event.payload)
    assert "hunter2" not in repr(record)
    assert "secret-token" not in repr(record)
    assert "private-key" not in repr(record)
    assert "owner@example.com" not in repr(record)


async def test_same_request_replays_and_key_reuse_conflicts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """相同请求返回原结果，同键不同正文必须冲突。"""
    service = build_service(session_factory)
    command = draft_command()

    first = await service.save_draft(command)
    duplicate = await service.save_draft(command)

    assert duplicate.operation_id == first.operation_id
    assert duplicate.runbook_id == first.runbook_id
    assert duplicate.is_duplicate is True
    with pytest.raises(ConflictError, match="Idempotency key"):
        await service.save_draft(draft_command(title="Different content"))
    assert await count_records(session_factory, RunbookRecord) == 1
    assert (
        await count_records(
            session_factory,
            OutboxEventRecord,
        )
        == 1
    )


async def test_draft_update_uses_revision_and_rolls_back_stale_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """草稿更新应递增 revision，旧 revision 不产生操作或审计记录。"""
    service = build_service(session_factory)
    await service.save_draft(draft_command())

    updated = await service.save_draft(
        draft_command(
            expected_revision=1,
            idempotency_key="idem_draft_update",
            title="Updated checkout response",
        )
    )

    assert updated.revision == 2
    with pytest.raises(ConflictError, match="revision"):
        await service.save_draft(
            draft_command(
                expected_revision=1,
                idempotency_key="idem_stale_update",
                title="Stale update",
            )
        )
    assert (
        await count_records(
            session_factory,
            RunbookOperationRecord,
        )
        == 2
    )
    assert (
        await count_records(
            session_factory,
            OutboxEventRecord,
        )
        == 2
    )


async def test_publish_switches_single_active_version_atomically(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """发布 v2 时必须归档 v1，检索侧只能看到新版本。"""
    clock = MutableClock()
    service = build_service(session_factory, clock=clock)
    await service.save_draft(draft_command())
    clock.value += timedelta(minutes=1)
    first_published = await service.publish(publish_command())
    assert first_published.revision == 2

    clock.value += timedelta(minutes=1)
    await service.save_draft(
        draft_command(
            version="v2",
            expected_revision=2,
            idempotency_key="idem_draft_v2",
        )
    )
    clock.value += timedelta(minutes=1)
    second_published = await service.publish(
        publish_command(
            version="v2",
            expected_revision=3,
            idempotency_key="idem_publish_v2",
        )
    )

    assert second_published.status == RunbookStatus.PUBLISHED.value
    async with session_factory() as session:
        records = (
            await session.scalars(select(RunbookRecord).order_by(RunbookRecord.version))
        ).all()
    assert [(item.version, item.status) for item in records] == [
        ("v1", RunbookStatus.ARCHIVED.value),
        ("v2", RunbookStatus.PUBLISHED.value),
    ]
    assert records[0].revision == 4
    async with session_factory() as session:
        head = await session.scalar(select(RunbookHeadRecord))
    assert head is not None
    assert head.revision == 4
    assert head.draft_runbook_id is None
    assert head.published_runbook_id == second_published.runbook_id
    result = await SQLAlchemyRunbookSearch(session_factory).search(
        "tenant_001",
        "checkout-api",
        5,
    )
    assert [item.version for item in result.items] == ["v2"]


async def test_published_version_is_immutable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """已经发布的具体版本不能被保存草稿接口覆盖。"""
    service = build_service(session_factory)
    await service.save_draft(draft_command())
    await service.publish(publish_command())

    with pytest.raises(ConflictError, match="immutable"):
        await service.save_draft(
            draft_command(
                expected_revision=2,
                idempotency_key="idem_overwrite_published",
            )
        )
    assert (
        await count_records(
            session_factory,
            OutboxEventRecord,
        )
        == 2
    )


async def test_clock_regression_rejects_publish_without_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """节点时钟回拨时不能生成倒序发布记录。"""
    clock = MutableClock()
    service = build_service(session_factory, clock=clock)
    await service.save_draft(draft_command())
    clock.value -= timedelta(minutes=1)

    with pytest.raises(ConflictError, match="time is stale"):
        await service.publish(publish_command())

    assert (
        await count_records(
            session_factory,
            RunbookOperationRecord,
        )
        == 1
    )
    assert (
        await count_records(
            session_factory,
            OutboxEventRecord,
        )
        == 1
    )


async def test_outbox_conflict_rolls_back_runbook_and_operation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """审计事件主键冲突时业务草稿和幂等操作也必须回滚。"""
    identifiers = DeterministicIdentifiers(fixed_event_id="evt_fixed")
    service = build_service(
        session_factory,
        identifiers=identifiers,
    )
    await service.save_draft(draft_command())

    with pytest.raises(ConflictError):
        await service.save_draft(
            draft_command(
                expected_revision=1,
                idempotency_key="idem_draft_update",
                title="Updated content",
            )
        )
    assert await count_records(session_factory, RunbookRecord) == 1
    assert (
        await count_records(
            session_factory,
            RunbookOperationRecord,
        )
        == 1
    )


async def test_revision_is_shared_across_business_versions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """不同内容版本也必须服从同一个逻辑 Runbook 修订号。"""
    service = build_service(session_factory)
    await service.save_draft(draft_command())
    await service.publish(publish_command())

    with pytest.raises(ConflictError, match="revision"):
        await service.save_draft(
            draft_command(
                version="v2",
                expected_revision=0,
                idempotency_key="idem_stale_v2",
            )
        )

    created = await service.save_draft(
        draft_command(
            version="v2",
            expected_revision=2,
            idempotency_key="idem_current_v2",
        )
    )
    assert created.revision == 3


async def test_legacy_multiple_drafts_fail_head_bootstrap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """旧数据存在多个活动草稿时不能武断建立聚合指针。"""
    async with session_factory() as session:
        session.add_all(
            [
                RunbookRecord(
                    runbook_id=f"rbk_legacy_{index}",
                    runbook_key="checkout.error-rate",
                    tenant_id="tenant_001",
                    service_name="checkout-api",
                    title=f"Legacy draft {index}",
                    summary="Legacy content.",
                    version=f"v{index}",
                    status=RunbookStatus.DRAFT.value,
                    revision=1,
                    priority=10,
                    steps_json='["Inspect service."]',
                    tags_json='["checkout"]',
                    published_at=None,
                    updated_at=NOW,
                )
                for index in (1, 2)
            ]
        )
        await session.commit()

    with pytest.raises(PersistenceError, match="ambiguous"):
        await build_service(session_factory).save_draft(
            draft_command(
                version="v3",
                expected_revision=1,
                idempotency_key="idem_legacy_v3",
            )
        )

    assert (
        await count_records(
            session_factory,
            RunbookHeadRecord,
        )
        == 0
    )
    assert (
        await count_records(
            session_factory,
            RunbookOperationRecord,
        )
        == 0
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_revision": -1},
        {"steps": ()},
        {"tags": ("INVALID TAG",)},
        {"priority": 1001},
        {"summary": "Review dependencies\x7fforged"},
        {"trace_id": "trc_draft\x7fforged"},
        {"tenant_id": " tenant_001"},
    ],
)
def test_draft_command_rejects_invalid_input(
    changes: dict[str, Any],
) -> None:
    """非法容量、标签、版本和身份必须在应用调用前失败。"""
    values = {
        "tenant_id": "tenant_001",
        "runbook_key": "checkout.error-rate",
        "version": "v1",
        "service_name": "checkout-api",
        "title": "Checkout response",
        "summary": "Review dependencies.",
        "priority": 100,
        "steps": ("Inspect service.",),
        "tags": ("checkout",),
        "expected_revision": 0,
        "idempotency_key": "idem_001",
        "requested_by": "admin_001",
        "trace_id": "trc_001",
    }
    values.update(changes)

    with pytest.raises(AppValidationError):
        SaveRunbookDraftCommand(**values)


def test_publish_command_requires_existing_positive_revision() -> None:
    """发布不能使用创建语义的 revision=0。"""
    with pytest.raises(AppValidationError, match="positive"):
        publish_command(expected_revision=0)
    with pytest.raises(AppValidationError):
        publish_command(trace_id="trc_publish\x7fforged")
