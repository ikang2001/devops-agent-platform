import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyOutboxRepository,
    outbox_dispatch_store,
)
from devops_agent_platform.infrastructure.database.models.outbox import (
    OutboxEventRecord,
)

pytestmark = pytest.mark.live


def _database_url() -> str:
    url = os.getenv("DEVOPS_AGENT_TEST_POSTGRES_URL", "").strip()
    if not url:
        pytest.fail(
            "DEVOPS_AGENT_TEST_POSTGRES_URL is required in live mode",
            pytrace=False,
        )
    if not url.startswith("postgresql+psycopg://"):
        pytest.fail(
            "live PostgreSQL URL must use postgresql+psycopg://",
            pytrace=False,
        )
    return url


def _event(event_id: str, now: datetime) -> OutboxEvent:
    return OutboxEvent(
        event_id=event_id,
        tenant_id="step4_acceptance",
        aggregate_type="Acceptance",
        aggregate_id=event_id,
        event_type="acceptance.requested",
        schema_version=1,
        payload={"acceptance_id": event_id},
        occurred_at=now,
        trace_id=f"trace_{event_id}",
    )


def test_live_alembic_upgrade_reaches_head() -> None:
    """真实 PostgreSQL 必须能在线迁移到唯一 head。"""
    environment = os.environ.copy()
    environment["DEVOPS_AGENT_DATABASE_URL"] = _database_url()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        f"Alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    )


async def test_live_outbox_duplicate_claim_restart_and_fencing() -> None:
    """在真实事务和行锁上验证幂等、并发抢占与租约接管。"""
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    suffix = uuid4().hex[:16]
    event_id = f"accept_{suffix}"
    now = datetime.now(UTC) + timedelta(seconds=1)
    try:
        async with factory() as session:
            repository = SQLAlchemyOutboxRepository(session)
            await repository.add(_event(event_id, now))
            await session.commit()

        async with factory() as session:
            repository = SQLAlchemyOutboxRepository(session)
            with pytest.raises(ConflictError):
                await repository.add(_event(event_id, now))
            await session.rollback()

        first_store = outbox_dispatch_store.SQLAlchemyOutboxDispatchStore(
            factory
        )
        worker_results = await asyncio.gather(
            first_store.claim_batch(
                "acceptance_worker_a",
                now,
                now + timedelta(seconds=2),
                1,
                5,
            ),
            first_store.claim_batch(
                "acceptance_worker_b",
                now,
                now + timedelta(seconds=2),
                1,
                5,
            ),
        )
        claimed = [item for batch in worker_results for item in batch]
        assert len(claimed) == 1
        first_owner = (
            "acceptance_worker_a"
            if worker_results[0]
            else "acceptance_worker_b"
        )

        # 新建 Store 模拟进程宕机重启；状态只能来自数据库，不能依赖内存。
        restarted_store = (
            outbox_dispatch_store.SQLAlchemyOutboxDispatchStore(factory)
        )
        takeover_time = now + timedelta(seconds=3)
        taken_over = await restarted_store.claim_batch(
            "acceptance_worker_restarted",
            takeover_time,
            takeover_time + timedelta(seconds=10),
            1,
            5,
        )
        assert [item.event.event_id for item in taken_over] == [event_id]
        assert taken_over[0].attempts == 2

        with pytest.raises(ConflictError, match="ownership lost"):
            await first_store.mark_published(event_id, first_owner, takeover_time)
        await restarted_store.mark_published(
            event_id,
            "acceptance_worker_restarted",
            takeover_time,
        )

        async with factory() as session:
            status = await session.scalar(
                text(
                    "SELECT status FROM outbox_events "
                    "WHERE event_id = :event_id"
                ),
                {"event_id": event_id},
            )
            revision = await session.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        assert status == "PUBLISHED"
        assert isinstance(revision, str) and revision
    finally:
        async with factory() as session:
            await session.execute(
                delete(OutboxEventRecord).where(
                    OutboxEventRecord.event_id == event_id
                )
            )
            await session.commit()
        await engine.dispose()
