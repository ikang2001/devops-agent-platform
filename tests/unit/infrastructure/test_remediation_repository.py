from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.domain.models.remediation import (
    RemediationPlan,
    RemediationRisk,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyRemediationPlanRepository,
)
from devops_agent_platform.infrastructure.database.base import Base

NOW = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)


def build_plan(plan_id: str = "rmp_001") -> RemediationPlan:
    return RemediationPlan(
        remediation_plan_id=plan_id,
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        incident_id="inc_001",
        action_key="restart_inventory",
        target="inventory",
        expected_effect="Restore inventory availability.",
        risk=RemediationRisk.MEDIUM,
        rollback_action_key="restore_inventory_revision",
        evidence_ids=("evd_001", "evd_002", "evd_003", "evd_004"),
        dry_run_summary="Action and rollback are valid.",
        created_by="admin_author",
        created_at=NOW,
        trace_id="trc_remediation_001",
        create_idempotency_key_hash="a" * 64,
        create_request_hash="b" * 64,
    )


async def test_repository_round_trip_and_versioned_replace() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            repository = SQLAlchemyRemediationPlanRepository(session)
            await repository.save(build_plan())
            await session.commit()

        async with factory() as session:
            repository = SQLAlchemyRemediationPlanRepository(session)
            restored = await repository.get_by_id("tenant_001", "rmp_001")
            by_key = await repository.get_by_create_idempotency_key_hash(
                "tenant_001",
                "a" * 64,
            )
            assert restored is not None
            assert by_key is not None
            assert restored.execution_attempt == 0
            assert restored.execution_lease_expires_at is None
            approved = restored.decide(
                approved=True,
                requested_by="admin_reviewer",
                reason="Reviewed.",
                decided_at=NOW,
                trace_id="trc_approve_001",
                idempotency_key="approve-remediation-001",
            )
            await repository.replace(approved, expected_version=1)
            await session.commit()

        async with factory() as session:
            repository = SQLAlchemyRemediationPlanRepository(session)
            updated = await repository.get_by_id("tenant_001", "rmp_001")
            assert updated is not None
            assert updated.version == 2
            # 带租约的 EXECUTING 必须可 round-trip，并进入 stale 扫描。
            started = updated.start_execution(
                requested_by="admin_executor",
                started_at=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
                trace_id="trc_execute_001",
                idempotency_key="execute-remediation-001",
            )
            await repository.replace(started, expected_version=2)
            await session.commit()

        async with factory() as session:
            repository = SQLAlchemyRemediationPlanRepository(session)
            executing = await repository.get_by_id("tenant_001", "rmp_001")
            assert executing is not None
            assert executing.status.value == "EXECUTING"
            assert executing.execution_attempt == 1
            assert executing.execution_lease_expires_at is not None
            stale = await repository.list_stale_execution(
                now=NOW + timedelta(seconds=31),
                limit=10,
            )
            assert len(stale) == 1
            assert stale[0].remediation_plan_id == "rmp_001"
            with pytest.raises(ConflictError, match="version conflict"):
                await repository.replace(executing, expected_version=1)
    finally:
        await engine.dispose()
