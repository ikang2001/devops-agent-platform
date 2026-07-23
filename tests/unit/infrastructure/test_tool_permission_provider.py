from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.exceptions import (
    PermissionDataSourceError,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyToolPermissionProvider,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.tool_permission import (
    ToolPermissionGrantRecord,
    ToolPermissionTagRecord,
)

NOW = datetime(2026, 6, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建权限Provider测试使用的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def add_grant(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    grant_id: str = "grant_001",
    tenant_id: str = "tenant_001",
    operator_id: str = "operator_001",
    permission_tags: tuple[str, ...] = (
        "logs:read",
        "tenant:observe",
    ),
    expires_at: datetime | None = NOW + timedelta(hours=1),
    revoked_at: datetime | None = None,
    created_at: datetime = NOW,
) -> None:
    """持久化一个授权主体及其规范化标签。"""
    async with session_factory() as session:
        session.add(
            ToolPermissionGrantRecord(
                grant_id=grant_id,
                tenant_id=tenant_id,
                operator_id=operator_id,
                expires_at=expires_at,
                revoked_at=revoked_at,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        await session.flush()
        session.add_all(
            ToolPermissionTagRecord(
                grant_id=grant_id,
                permission_tag=permission_tag,
            )
            for permission_tag in permission_tags
        )
        await session.commit()


async def test_provider_loads_normalized_permission_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Provider应将多行标签合并为不可变授权快照。"""
    await add_grant(session_factory)
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    grant = await provider.get_grant("tenant_001", "operator_001")

    assert grant is not None
    assert grant.tenant_id == "tenant_001"
    assert grant.operator_id == "operator_001"
    assert grant.permission_tags == frozenset({"logs:read", "tenant:observe"})
    assert grant.expires_at == NOW + timedelta(hours=1)


async def test_provider_preserves_empty_permission_set(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """存在主体但没有标签时返回空授权，而不是伪装成主体不存在。"""
    await add_grant(session_factory, permission_tags=())
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    grant = await provider.get_grant("tenant_001", "operator_001")

    assert grant is not None
    assert grant.permission_tags == frozenset()


async def test_lookup_is_isolated_by_tenant_and_operator(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """相同操作者在不同租户的标签不得互相泄漏。"""
    await add_grant(
        session_factory,
        grant_id="grant_tenant_a",
        tenant_id="tenant_a",
        permission_tags=("logs:read",),
    )
    await add_grant(
        session_factory,
        grant_id="grant_tenant_b",
        tenant_id="tenant_b",
        permission_tags=("metrics:read",),
    )
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    tenant_a = await provider.get_grant("tenant_a", "operator_001")
    tenant_b = await provider.get_grant("tenant_b", "operator_001")

    assert tenant_a is not None
    assert tenant_a.permission_tags == frozenset({"logs:read"})
    assert tenant_b is not None
    assert tenant_b.permission_tags == frozenset({"metrics:read"})


async def test_revoked_grant_is_hidden_and_regrant_can_be_loaded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """撤销历史可以保留，但运行时只读取新的未撤销授权。"""
    await add_grant(
        session_factory,
        grant_id="grant_revoked",
        permission_tags=("logs:read",),
        revoked_at=NOW + timedelta(minutes=1),
    )
    await add_grant(
        session_factory,
        grant_id="grant_active",
        permission_tags=("metrics:read",),
        created_at=NOW + timedelta(minutes=2),
        expires_at=NOW + timedelta(hours=2),
    )
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    grant = await provider.get_grant("tenant_001", "operator_001")

    assert grant is not None
    assert grant.permission_tags == frozenset({"metrics:read"})


async def test_two_active_grants_violate_database_constraint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """部分唯一索引必须阻止同一主体同时存在两个有效授权。"""
    await add_grant(session_factory, grant_id="grant_first")

    with pytest.raises(IntegrityError):
        await add_grant(session_factory, grant_id="grant_second")


async def test_missing_or_only_revoked_grant_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """不存在和仅存在撤销历史都返回无当前授权。"""
    await add_grant(
        session_factory,
        revoked_at=NOW + timedelta(minutes=1),
    )
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    assert await provider.get_grant("tenant_001", "operator_001") is None
    assert await provider.get_grant("tenant_001", "operator_missing") is None


async def test_expired_grant_is_returned_for_checker_clock_decision(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Provider返回过期时间，由Checker使用统一时钟作最终判断。"""
    await add_grant(
        session_factory,
        created_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    grant = await provider.get_grant("tenant_001", "operator_001")

    assert grant is not None
    assert grant.expires_at == NOW - timedelta(hours=1)


async def test_query_parameters_do_not_allow_sql_injection(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """类似SQL片段的合法字符串仍按普通参数精确匹配。"""
    await add_grant(session_factory)
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    grant = await provider.get_grant(
        "tenant_001' OR 1=1 --",
        "operator_001",
    )

    assert grant is None


async def test_invalid_query_key_fails_before_database_access(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """空值、首尾空白、控制字符和超长查询键应在SQL执行前失败。"""
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    with pytest.raises(AppValidationError):
        await provider.get_grant("", "operator_001")
    with pytest.raises(AppValidationError):
        await provider.get_grant(" tenant_001", "operator_001")
    with pytest.raises(AppValidationError):
        await provider.get_grant("tenant_001\nforged", "operator_001")
    with pytest.raises(AppValidationError):
        await provider.get_grant("tenant_001\x7fforged", "operator_001")
    with pytest.raises(AppValidationError):
        await provider.get_grant("tenant_001", "operator_001\x7fforged")
    with pytest.raises(AppValidationError):
        await provider.get_grant("tenant_001", "x" * 129)


@pytest.mark.parametrize(
    "permission_tag",
    [
        "logs:read\nforged",
        "logs:read\x7fforged",
    ],
)
async def test_dirty_permission_tag_fails_with_source_error(
    session_factory: async_sessionmaker[AsyncSession],
    permission_tag: str,
) -> None:
    """历史脏权限标签不能作为可信授权快照返回。"""
    await add_grant(
        session_factory,
        permission_tags=(permission_tag,),
    )
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    with pytest.raises(PermissionDataSourceError, match="load"):
        await provider.get_grant("tenant_001", "operator_001")


async def test_excessive_permission_tags_fail_with_source_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """异常膨胀的权限集合必须有界，不能全部带入Agent进程。"""
    await add_grant(
        session_factory,
        permission_tags=tuple(f"permission:{index:04d}" for index in range(1025)),
    )
    provider = SQLAlchemyToolPermissionProvider(session_factory)

    with pytest.raises(PermissionDataSourceError, match="load"):
        await provider.get_grant("tenant_001", "operator_001")


async def test_database_failure_is_mapped_to_permission_source_error() -> None:
    """数据库表不可用时返回稳定503语义并保留异常链。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = SQLAlchemyToolPermissionProvider(factory)
    try:
        with pytest.raises(PermissionDataSourceError) as exc_info:
            await provider.get_grant("tenant_001", "operator_001")
    finally:
        await engine.dispose()

    assert exc_info.value.code == "PERMISSION_SOURCE_UNAVAILABLE"
    assert isinstance(exc_info.value.__cause__, Exception)
