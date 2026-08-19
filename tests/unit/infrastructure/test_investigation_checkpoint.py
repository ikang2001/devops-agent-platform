from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devops_agent_platform.domain.models.investigation import (
    InvestigationBudget,
    InvestigationState,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyInvestigationCheckpoint,
)
from devops_agent_platform.infrastructure.database.base import Base


async def test_sqlalchemy_checkpoint_round_trips_and_rejects_stale_state() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    checkpoint = SQLAlchemyInvestigationCheckpoint(factory)
    try:
        state = InvestigationState(
            incident_id="incident-1",
            tenant_id="tenant-1",
            service_name="checkout-api",
            remaining_budget=InvestigationBudget(max_steps=3),
            started_at=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
        )
        await checkpoint.save(state)
        state.record_tool("metrics.query@v1", succeeded=True, evidence_ids=("ev-1",))
        await checkpoint.save(state)

        restored = await checkpoint.load("tenant-1", "incident-1")
        assert restored is not None
        assert restored.checkpoint_version == state.checkpoint_version
        assert restored.evidence_ids == ["ev-1"]

        stale = InvestigationState(
            incident_id="incident-1",
            tenant_id="tenant-1",
            service_name="checkout-api",
            remaining_budget=InvestigationBudget(max_steps=3),
            started_at=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
        )
        await checkpoint.save(stale)
        restored_after_stale = await checkpoint.load("tenant-1", "incident-1")
        assert restored_after_stale is not None
        assert restored_after_stale.evidence_ids == ["ev-1"]
    finally:
        await engine.dispose()
