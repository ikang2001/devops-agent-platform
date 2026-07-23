import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.commands.tool_permissions import (
    RevokeToolPermissionsCommand,
    SetToolPermissionsCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.queries.tool_permissions import (
    GetToolPermissionsQuery,
)
from devops_agent_platform.application.services.tool_permission_admin_service import (
    ToolPermissionAdminService,
)
from devops_agent_platform.domain.enums import ToolPermissionChangeAction
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyToolPermissionAdminStore,
    SQLAlchemyToolPermissionProvider,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)
from devops_agent_platform.infrastructure.database.models.tool_permission import (
    ToolPermissionGrantRecord,
    ToolPermissionOperationRecord,
    ToolPermissionTagRecord,
)
from devops_agent_platform.ports.tool_permissions import ToolPermissionMutation

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


class DeterministicIdentifiers:
    """为权限管理测试生成可预测标识。"""

    def __init__(self, *, fixed_event_id: str | None = None) -> None:
        self._grant_sequence = 0
        self._operation_sequence = 0
        self._event_sequence = 0
        self._fixed_event_id = fixed_event_id

    def new_permission_grant_id(self) -> str:
        self._grant_sequence += 1
        return f"pgr_{self._grant_sequence:03d}"

    def new_permission_operation_id(self) -> str:
        self._operation_sequence += 1
        return f"pop_{self._operation_sequence:03d}"

    def new_event_id(self) -> str:
        if self._fixed_event_id is not None:
            return self._fixed_event_id
        self._event_sequence += 1
        return f"evt_{self._event_sequence:03d}"


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建权限管理应用服务测试使用的隔离数据库。"""
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
    identifiers: DeterministicIdentifiers | None = None,
) -> ToolPermissionAdminService:
    """装配真实SQLAlchemy Store和固定时钟。"""
    return ToolPermissionAdminService(
        store=SQLAlchemyToolPermissionAdminStore(session_factory),
        identifier_generator=identifiers or DeterministicIdentifiers(),
        clock=lambda: NOW,
    )


def set_command(
    *,
    permission_tags: tuple[str, ...] = (
        "tenant:observe",
        "logs:read",
    ),
    expected_version: int = 0,
    idempotency_key: str = "idem_set_001",
    trace_id: str = "trc_001",
) -> SetToolPermissionsCommand:
    """构造授权或替换命令。"""
    return SetToolPermissionsCommand(
        tenant_id="tenant_001",
        operator_id="operator_001",
        permission_tags=permission_tags,
        expires_at=NOW + timedelta(hours=1),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        requested_by="admin_001",
        trace_id=trace_id,
    )


def revoke_command(
    *,
    expected_version: int,
    idempotency_key: str = "idem_revoke_001",
    trace_id: str = "trc_revoke",
) -> RevokeToolPermissionsCommand:
    """构造权限撤销命令。"""
    return RevokeToolPermissionsCommand(
        tenant_id="tenant_001",
        operator_id="operator_001",
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        requested_by="admin_001",
        trace_id=trace_id,
    )


async def count_records(
    session_factory: async_sessionmaker[AsyncSession],
    model: type,
) -> int:
    """统计指定ORM表记录数。"""
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_initial_set_is_atomic_and_emits_audit_outbox(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """首次授权应原子写入快照、标签、幂等结果和审计事件。"""
    service = build_service(session_factory)

    result = await service.set_permissions(set_command())

    assert result.version == 1
    assert result.action == "SET"
    assert result.is_duplicate is False
    provider = SQLAlchemyToolPermissionProvider(session_factory)
    grant = await provider.get_grant("tenant_001", "operator_001")
    assert grant is not None
    assert grant.permission_tags == frozenset({"logs:read", "tenant:observe"})
    assert (
        await count_records(
            session_factory,
            ToolPermissionOperationRecord,
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
        event = await session.scalar(select(OutboxEventRecord))
    assert event is not None
    assert event.event_type == "tool_permission.set"
    assert event.payload["result_version"] == 1
    assert event.payload["permission_tags"] == [
        "logs:read",
        "tenant:observe",
    ]
    assert "idempotency_key" not in event.payload


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("tenant_id", "tenant_001\nforged"),
        ("operator_id", "operator_001\tforged"),
        ("idempotency_key", "idem_set_001\rforged"),
        ("idempotency_key", "idem_set_001\x7fforged"),
        ("requested_by", "admin_001\nforged"),
        ("requested_by", "admin_001\x7fforged"),
        ("trace_id", "trc_001\tforged"),
    ],
)
def test_permission_commands_reject_control_character_identities(
    field_name: str,
    value: str,
) -> None:
    """权限管理命令不能把污染身份写入权限表或审计事件。"""
    kwargs = {
        "tenant_id": "tenant_001",
        "operator_id": "operator_001",
        "permission_tags": ("logs:read",),
        "expires_at": NOW + timedelta(hours=1),
        "expected_version": 0,
        "idempotency_key": "idem_set_001",
        "requested_by": "admin_001",
        "trace_id": "trc_001",
    }
    kwargs[field_name] = value

    with pytest.raises(AppValidationError, match="invalid"):
        SetToolPermissionsCommand(**kwargs)

    revoke_kwargs = {
        key: kwargs[key]
        for key in (
            "tenant_id",
            "operator_id",
            "expected_version",
            "idempotency_key",
            "requested_by",
            "trace_id",
        )
    }
    with pytest.raises(AppValidationError, match="invalid"):
        RevokeToolPermissionsCommand(**revoke_kwargs)


def test_permission_commands_reject_control_character_permission_tags() -> None:
    """权限标签是授权事实，也不能携带不可见控制字符。"""
    with pytest.raises(AppValidationError, match="permission tag"):
        set_command(permission_tags=("logs:read\x7f",))


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("tenant_id", "tenant_001\nforged"),
        ("tenant_id", "tenant_001\x7fforged"),
        ("operator_id", "operator_001\tforged"),
        ("operator_id", "operator_001\x7fforged"),
    ],
)
def test_permission_query_rejects_control_character_identities(
    field_name: str,
    value: str,
) -> None:
    """权限快照查询键也必须保持单行。"""
    kwargs = {
        "tenant_id": "tenant_001",
        "operator_id": "operator_001",
    }
    kwargs[field_name] = value

    with pytest.raises(AppValidationError, match="invalid"):
        GetToolPermissionsQuery(**kwargs)


async def test_get_current_returns_active_snapshot_and_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """管理端读取权限快照应返回确定性标签顺序和当前ETag版本。"""
    service = build_service(session_factory)
    await service.set_permissions(set_command())

    snapshot = await service.get_current(
        GetToolPermissionsQuery(
            tenant_id="tenant_001",
            operator_id="operator_001",
        )
    )

    assert snapshot.active is True
    assert snapshot.version == 1
    assert snapshot.grant_id == "pgr_001"
    assert snapshot.permission_tags == ("logs:read", "tenant:observe")
    assert snapshot.expires_at == NOW + timedelta(hours=1)
    assert snapshot.to_dict()["permission_tags"] == [
        "logs:read",
        "tenant:observe",
    ]


async def test_get_current_after_revoke_preserves_state_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """撤销后没有活动授权，但读接口仍要返回最新版本供下一次写入使用。"""
    service = build_service(session_factory)
    await service.set_permissions(set_command())
    await service.revoke_permissions(revoke_command(expected_version=1))

    snapshot = await service.get_current(
        GetToolPermissionsQuery(
            tenant_id="tenant_001",
            operator_id="operator_001",
        )
    )

    assert snapshot.active is False
    assert snapshot.version == 2
    assert snapshot.grant_id is None
    assert snapshot.permission_tags == ()
    assert snapshot.expires_at is None


async def test_get_current_missing_operator_returns_empty_version_zero(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """从未授权过的操作者读取为空快照，初始写入版本为0。"""
    service = build_service(session_factory)

    snapshot = await service.get_current(
        GetToolPermissionsQuery(
            tenant_id="tenant_001",
            operator_id="operator_001",
        )
    )

    assert snapshot.active is False
    assert snapshot.version == 0
    assert snapshot.permission_tags == ()


async def test_same_idempotency_request_returns_original_result_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """标签顺序不同但语义相同的重放不得重复写入。"""
    service = build_service(session_factory)
    first = await service.set_permissions(set_command())
    duplicate = await service.set_permissions(
        set_command(
            permission_tags=("logs:read", "tenant:observe"),
            trace_id="trc_retry",
        )
    )

    assert duplicate.operation_id == first.operation_id
    assert duplicate.grant_id == first.grant_id
    assert duplicate.version == first.version
    assert duplicate.trace_id == "trc_retry"
    assert duplicate.is_duplicate is True
    assert (
        await count_records(
            session_factory,
            ToolPermissionGrantRecord,
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


async def test_concurrent_same_request_has_single_committed_operation(
    tmp_path: Path,
) -> None:
    """并发相同请求只能提交一次，其余调用恢复同一幂等结果。"""
    database_path = (tmp_path / "permissions.sqlite").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = build_service(factory)
    command = set_command()
    try:
        first, second = await asyncio.gather(
            service.set_permissions(command),
            service.set_permissions(command),
        )
    finally:
        await engine.dispose()

    assert first.operation_id == second.operation_id
    assert first.grant_id == second.grant_id
    assert {first.is_duplicate, second.is_duplicate} == {False, True}


async def test_reused_idempotency_key_for_other_request_conflicts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一幂等键不能被复用于另一组权限。"""
    service = build_service(session_factory)
    await service.set_permissions(set_command())

    with pytest.raises(ConflictError, match="Idempotency key"):
        await service.set_permissions(set_command(permission_tags=("metrics:read",)))


async def test_replace_preserves_history_and_increments_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """替换权限应撤销旧快照并创建新版本，而不是覆盖历史标签。"""
    service = build_service(session_factory)
    first = await service.set_permissions(set_command())
    second = await service.set_permissions(
        set_command(
            permission_tags=("metrics:read",),
            expected_version=1,
            idempotency_key="idem_set_002",
        )
    )

    assert first.version == 1
    assert second.version == 2
    provider = SQLAlchemyToolPermissionProvider(session_factory)
    active = await provider.get_grant("tenant_001", "operator_001")
    assert active is not None
    assert active.permission_tags == frozenset({"metrics:read"})

    async with session_factory() as session:
        grants = (
            await session.scalars(
                select(ToolPermissionGrantRecord).order_by(
                    ToolPermissionGrantRecord.version
                )
            )
        ).all()
        old_tags = (
            await session.scalars(
                select(ToolPermissionTagRecord.permission_tag).where(
                    ToolPermissionTagRecord.grant_id == first.grant_id
                )
            )
        ).all()
    assert len(grants) == 2
    assert grants[0].revoked_at == NOW
    assert grants[1].revoked_at is None
    assert set(old_tags) == {"logs:read", "tenant:observe"}


async def test_stale_version_rolls_back_without_audit_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """过期期望版本不得修改权限，也不得产生审计Outbox。"""
    service = build_service(session_factory)
    await service.set_permissions(set_command())

    with pytest.raises(ConflictError, match="version conflict"):
        await service.set_permissions(
            set_command(
                permission_tags=("metrics:read",),
                expected_version=0,
                idempotency_key="idem_stale",
            )
        )

    assert (
        await count_records(
            session_factory,
            ToolPermissionGrantRecord,
        )
        == 1
    )
    assert (
        await count_records(
            session_factory,
            ToolPermissionOperationRecord,
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


async def test_revoke_hides_active_grant_and_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """撤销应递增版本、隐藏授权，并且同请求重放只写一次。"""
    service = build_service(session_factory)
    await service.set_permissions(set_command())

    revoked = await service.revoke_permissions(revoke_command(expected_version=1))
    duplicate = await service.revoke_permissions(
        revoke_command(
            expected_version=1,
            trace_id="trc_revoke_retry",
        )
    )

    assert revoked.version == 2
    assert revoked.action == "REVOKE"
    assert duplicate.operation_id == revoked.operation_id
    assert duplicate.is_duplicate is True
    assert duplicate.trace_id == "trc_revoke_retry"
    provider = SQLAlchemyToolPermissionProvider(session_factory)
    assert await provider.get_grant("tenant_001", "operator_001") is None
    assert (
        await count_records(
            session_factory,
            OutboxEventRecord,
        )
        == 2
    )


async def test_revoke_missing_grant_writes_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """没有有效授权时撤销应明确失败且不留下操作记录。"""
    service = build_service(session_factory)

    with pytest.raises(ResourceNotFound):
        await service.revoke_permissions(revoke_command(expected_version=0))

    assert (
        await count_records(
            session_factory,
            ToolPermissionOperationRecord,
        )
        == 0
    )
    assert (
        await count_records(
            session_factory,
            OutboxEventRecord,
        )
        == 0
    )


async def test_outbox_conflict_rolls_back_permission_replacement(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """审计事件写入失败时，旧授权必须保持有效。"""
    identifiers = DeterministicIdentifiers(fixed_event_id="evt_fixed")
    service = build_service(session_factory, identifiers)
    first = await service.set_permissions(set_command())

    with pytest.raises(ConflictError):
        await service.set_permissions(
            set_command(
                permission_tags=("metrics:read",),
                expected_version=1,
                idempotency_key="idem_set_002",
            )
        )

    provider = SQLAlchemyToolPermissionProvider(session_factory)
    active = await provider.get_grant("tenant_001", "operator_001")
    assert active is not None
    assert active.permission_tags == frozenset({"logs:read", "tenant:observe"})
    assert (
        await count_records(
            session_factory,
            ToolPermissionGrantRecord,
        )
        == 1
    )
    assert (
        await count_records(
            session_factory,
            ToolPermissionOperationRecord,
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
    assert first.version == 1


async def test_admin_store_rejects_control_character_mutation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Store端口直连也不能绕过命令层写入污染权限审计事实。"""
    store = SQLAlchemyToolPermissionAdminStore(session_factory)
    mutation = ToolPermissionMutation(
        operation_id="pop_001",
        new_grant_id="pgr_001",
        tenant_id="tenant_001",
        operator_id="operator_001\x7fforged",
        permission_tags=("logs:read",),
        expires_at=NOW + timedelta(hours=1),
        expected_version=0,
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        requested_by="admin_001",
        trace_id="trc_001",
        action=ToolPermissionChangeAction.SET,
        occurred_at=NOW,
    )
    event = OutboxEvent(
        event_id="evt_001",
        tenant_id="tenant_001",
        aggregate_type="ToolPermissionOperation",
        aggregate_id=mutation.operation_id,
        event_type="tool_permission.set",
        schema_version=1,
        payload={
            "operation_id": mutation.operation_id,
            "tenant_id": "tenant_001",
            "operator_id": mutation.operator_id,
        },
        occurred_at=mutation.occurred_at,
        trace_id=mutation.trace_id,
    )

    with pytest.raises(AppValidationError, match="operator_id"):
        await store.apply(mutation, event)

    assert (
        await count_records(
            session_factory,
            ToolPermissionOperationRecord,
        )
        == 0
    )


async def test_expired_at_or_before_now_is_rejected_before_store(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """过期授权必须在任何数据库写入前被拒绝。"""
    service = build_service(session_factory)
    command = SetToolPermissionsCommand(
        tenant_id="tenant_001",
        operator_id="operator_001",
        permission_tags=("logs:read",),
        expires_at=NOW,
        expected_version=0,
        idempotency_key="idem_expired",
        requested_by="admin_001",
        trace_id="trc_001",
    )

    with pytest.raises(AppValidationError, match="later than now"):
        await service.set_permissions(command)

    assert (
        await count_records(
            session_factory,
            ToolPermissionGrantRecord,
        )
        == 0
    )
