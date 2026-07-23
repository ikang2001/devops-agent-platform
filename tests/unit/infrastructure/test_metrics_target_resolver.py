from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.exceptions import MetricsSourceError
from devops_agent_platform.domain.enums import AlertSeverity, IncidentStatus
from devops_agent_platform.domain.exceptions import ResourceNotFound
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyMetricsTargetResolver,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[
    async_sessionmaker[AsyncSession]
]:
    """创建指标目标解析测试使用的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            IncidentMapper.to_record(
                Incident(
                    incident_id="inc_001",
                    tenant_id="tenant_001",
                    service_name="checkout-api",
                    severity=AlertSeverity.CRITICAL,
                    status=IncidentStatus.ANALYZING,
                    title="Checkout errors",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        )
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_resolver_loads_tenant_scoped_service_target(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """解析器只返回同租户事故对应的服务名。"""
    resolver = SQLAlchemyMetricsTargetResolver(session_factory)

    target = await resolver.resolve("tenant_001", "inc_001")

    assert target.tenant_id == "tenant_001"
    assert target.incident_id == "inc_001"
    assert target.service_name == "checkout-api"


async def test_cross_tenant_or_missing_incident_is_not_found(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """跨租户探测与不存在事故使用相同未找到语义。"""
    resolver = SQLAlchemyMetricsTargetResolver(session_factory)

    with pytest.raises(ResourceNotFound):
        await resolver.resolve("tenant_other", "inc_001")
    with pytest.raises(ResourceNotFound):
        await resolver.resolve("tenant_001", "inc_missing")


async def test_database_failure_is_mapped_to_metrics_source_error() -> None:
    """数据库结构不可用时返回稳定指标源异常。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    resolver = SQLAlchemyMetricsTargetResolver(factory)
    try:
        with pytest.raises(MetricsSourceError) as exc_info:
            await resolver.resolve("tenant_001", "inc_001")
    finally:
        await engine.dispose()

    assert isinstance(exc_info.value.__cause__, Exception)
