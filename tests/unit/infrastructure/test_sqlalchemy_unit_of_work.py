import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.domain.enums import AlertSeverity, IncidentStatus
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.infrastructure.adapters.sqlalchemy.unit_of_work import (
    SQLAlchemyUnitOfWork,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.models.alert import AlertRecord
from devops_agent_platform.infrastructure.database.models.incident import IncidentRecord


class TrackingAsyncSession(AsyncSession):
    """记录 Session 关闭次数，用于验证连接资源生命周期。"""

    close_count = 0

    async def close(self) -> None:
        type(self).close_count += 1
        await super().close()


class FailingCleanupSession:
    """模拟业务失败后回滚再次失败的数据库 Session。"""

    def __init__(self) -> None:
        self.close_count = 0

    def in_transaction(self) -> bool:
        return True

    async def rollback(self) -> None:
        raise SQLAlchemyError("password=database-secret")

    async def close(self) -> None:
        self.close_count += 1


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建使用可追踪 Session 的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    TrackingAsyncSession.close_count = 0
    factory = async_sessionmaker(
        engine,
        class_=TrackingAsyncSession,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


def build_alert(alert_id: str = "alt_uow_001") -> Alert:
    """构造事务测试使用的有效告警。"""
    return Alert(
        alert_id=alert_id,
        tenant_id="tenant_001",
        source="alertmanager",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        summary="Checkout error rate is above threshold",
        starts_at=datetime(2026, 6, 27, 8, 0, tzinfo=UTC),
        fingerprint="fp_checkout_error_rate",
        external_event_id="evt_uow_001",
    )


def build_incident(incident_id: str = "inc_uow_001") -> Incident:
    """构造事务测试使用的有效事故。"""
    now = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
    return Incident(
        incident_id=incident_id,
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.OPEN,
        title="Checkout error rate is above threshold",
        created_at=now,
        updated_at=now,
    )


async def get_alert_record(
    session_factory: async_sessionmaker[AsyncSession],
    alert_id: str,
) -> AlertRecord | None:
    """使用独立 Session 查询已提交数据，避免读取事务内缓存。"""
    async with session_factory() as session:
        return await session.scalar(
            select(AlertRecord).where(AlertRecord.alert_id == alert_id)
        )


async def get_incident_record(
    session_factory: async_sessionmaker[AsyncSession],
    incident_id: str,
) -> IncidentRecord | None:
    """使用独立 Session 查询事故记录。"""
    async with session_factory() as session:
        return await session.scalar(
            select(IncidentRecord).where(
                IncidentRecord.incident_id == incident_id
            )
        )


async def test_explicit_commit_persists_alert_and_incident_atomically(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert = build_alert()
    incident = build_incident()
    unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    async with unit_of_work:
        await unit_of_work.alerts.save(alert)
        await unit_of_work.incidents.save(incident)
        await unit_of_work.commit()

    assert TrackingAsyncSession.close_count == 1
    assert await get_alert_record(session_factory, alert.alert_id) is not None
    assert (
        await get_incident_record(session_factory, incident.incident_id)
        is not None
    )


async def test_normal_exit_without_commit_rolls_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert = build_alert()

    async with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        await unit_of_work.alerts.save(alert)

    assert TrackingAsyncSession.close_count == 1
    assert await get_alert_record(session_factory, alert.alert_id) is None


async def test_business_exception_rolls_back_and_propagates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert = build_alert()
    incident = build_incident()

    with pytest.raises(RuntimeError, match="simulated business failure"):
        async with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
            await unit_of_work.alerts.save(alert)
            await unit_of_work.incidents.save(incident)
            raise RuntimeError("simulated business failure")

    assert TrackingAsyncSession.close_count == 1
    assert await get_alert_record(session_factory, alert.alert_id) is None
    assert (
        await get_incident_record(session_factory, incident.incident_id)
        is None
    )


async def test_cleanup_failure_log_preserves_business_error_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """回滚二次失败应固定告警，同时保留最初业务异常。"""
    session = FailingCleanupSession()
    unit_of_work = SQLAlchemyUnitOfWork(
        lambda: session,  # type: ignore[arg-type]
    )

    with caplog.at_level(
        logging.ERROR,
        logger=(
            "devops_agent_platform.infrastructure.adapters.sqlalchemy."
            "unit_of_work"
        ),
    ):
        with pytest.raises(RuntimeError, match="business-secret"):
            async with unit_of_work:
                raise RuntimeError("token=business-secret")

    assert session.close_count == 1
    assert [record.getMessage() for record in caplog.records] == [
        "事务清理失败，保留原始业务异常"
    ]
    assert caplog.records[0].exc_info is None
    assert "business-secret" not in caplog.text
    assert "database-secret" not in caplog.text


async def test_transaction_cannot_be_completed_twice(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
        await unit_of_work.rollback()

        with pytest.raises(RuntimeError, match="already completed"):
            await unit_of_work.commit()


async def test_repository_access_outside_context_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.alerts

    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.workflow_runs

    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.evidence

    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.tool_invocations

    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.rca_reports


async def test_same_unit_of_work_cannot_be_nested(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    async with unit_of_work:
        with pytest.raises(RuntimeError, match="already active"):
            await unit_of_work.__aenter__()
