from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.exceptions import RunbookSourceError
from devops_agent_platform.domain.enums import RunbookStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.runbook import Runbook
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyRunbookSearch,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.runbook import (
    RunbookMapper,
)
from devops_agent_platform.infrastructure.database.models.runbook import (
    RunbookRecord,
)

NOW = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def build_runbook(
    runbook_id: str,
    *,
    tenant_id: str = "tenant_001",
    service_name: str = "checkout-api",
    status: RunbookStatus = RunbookStatus.PUBLISHED,
    priority: int = 10,
    updated_at: datetime = NOW,
    runbook_key: str | None = None,
    version: str = "v1",
) -> Runbook:
    """构造仓储排序和隔离测试所需的手册。"""
    return Runbook(
        runbook_id=runbook_id,
        runbook_key=runbook_key or runbook_id,
        tenant_id=tenant_id,
        service_name=service_name,
        title=f"Runbook {runbook_id}",
        summary="A reviewed operational procedure.",
        version=version,
        status=status,
        revision=1,
        priority=priority,
        steps=("Inspect the current service state.",),
        tags=("incident",),
        published_at=(None if status is RunbookStatus.DRAFT else updated_at),
        updated_at=updated_at,
    )


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建包含多租户、多状态 Runbook 的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                RunbookMapper.to_record(
                    build_runbook(
                        "rb_exact",
                        priority=1,
                        updated_at=NOW - timedelta(days=1),
                    )
                ),
                RunbookMapper.to_record(
                    build_runbook(
                        "rb_generic",
                        service_name="*",
                        priority=1000,
                    )
                ),
                RunbookMapper.to_record(
                    build_runbook(
                        "rb_draft",
                        status=RunbookStatus.DRAFT,
                    )
                ),
                RunbookMapper.to_record(
                    build_runbook(
                        "rb_other_tenant",
                        tenant_id="tenant_other",
                    )
                ),
                RunbookMapper.to_record(
                    build_runbook(
                        "rb_other_service",
                        service_name="payment-api",
                    )
                ),
            ]
        )
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_search_is_tenant_scoped_and_exact_service_first(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """服务专属手册必须先于高优先级通用手册，草稿不得返回。"""
    result = await SQLAlchemyRunbookSearch(session_factory).search(
        "tenant_001",
        "checkout-api",
        5,
    )

    assert [item.runbook_id for item in result.items] == [
        "rb_exact",
        "rb_generic",
    ]
    assert result.possibly_truncated is False


async def test_search_fetches_one_extra_record_to_report_truncation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """达到查询上限时应通过多取一条准确标记截断。"""
    result = await SQLAlchemyRunbookSearch(session_factory).search(
        "tenant_001",
        "checkout-api",
        1,
    )

    assert [item.runbook_id for item in result.items] == ["rb_exact"]
    assert result.possibly_truncated is True


async def test_search_rejects_invalid_limit_before_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """无界 Limit 不能进入 SQL 构造阶段。"""
    search = SQLAlchemyRunbookSearch(session_factory)

    with pytest.raises(AppValidationError):
        await search.search("tenant_001", "checkout-api", 51)


@pytest.mark.parametrize(
    ("tenant_id", "service_name"),
    [
        ("tenant_001\x7fforged", "checkout-api"),
        ("tenant_001", "checkout-api\x7fforged"),
    ],
)
async def test_search_rejects_del_contaminated_keys_before_database(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: str,
    service_name: str,
) -> None:
    """Runbook 检索键中的 DEL 不能进入 SQL 查询。"""
    search = SQLAlchemyRunbookSearch(session_factory)

    with pytest.raises(AppValidationError):
        await search.search(tenant_id, service_name, 5)


async def test_dirty_json_is_mapped_to_source_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """数据库脏数据不能作为可信 Runbook 返回。"""
    async with session_factory() as session:
        session.add(
            RunbookRecord(
                runbook_id="rb_dirty",
                runbook_key="dirty.runbook",
                tenant_id="tenant_001",
                service_name="checkout-api",
                title="Dirty runbook",
                summary="Invalid serialized steps.",
                version="v1",
                status=RunbookStatus.PUBLISHED.value,
                revision=1,
                priority=999,
                steps_json="{}",
                tags_json="[]",
                published_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()

    with pytest.raises(RunbookSourceError):
        await SQLAlchemyRunbookSearch(session_factory).search(
            "tenant_001",
            "checkout-api",
            5,
        )


async def test_only_one_version_per_runbook_key_can_be_published(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一逻辑手册不能同时存在两个已发布版本。"""
    first = build_runbook(
        "rb_release_v1",
        runbook_key="checkout.release",
    )
    second = build_runbook(
        "rb_release_v2",
        runbook_key="checkout.release",
        version="v2",
    )

    async with session_factory() as session:
        session.add_all(
            [
                RunbookMapper.to_record(first),
                RunbookMapper.to_record(second),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_database_failure_is_mapped_to_source_error() -> None:
    """表缺失等数据库故障应暴露稳定的依赖异常。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        with pytest.raises(RunbookSourceError):
            await SQLAlchemyRunbookSearch(factory).search(
                "tenant_001",
                "checkout-api",
                5,
            )
    finally:
        await engine.dispose()
