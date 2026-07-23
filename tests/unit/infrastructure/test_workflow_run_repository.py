from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    IncidentStatus,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyWorkflowRunRepository,
)
from devops_agent_platform.infrastructure.database.base import Base
from devops_agent_platform.infrastructure.database.mappers.incident import (
    IncidentMapper,
)
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)
from devops_agent_platform.infrastructure.database.models.workflow_run import (
    WorkflowRunRecord,
)

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """创建WorkflowRun仓储测试使用的隔离数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                IncidentMapper.to_record(
                    Incident(
                        incident_id="inc_001",
                        tenant_id="tenant_001",
                        service_name="checkout-api",
                        severity=AlertSeverity.CRITICAL,
                        status=IncidentStatus.OPEN,
                        title="Checkout API failure",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            )
            await session.commit()
        yield factory
    finally:
        await engine.dispose()


def build_workflow_run(
    workflow_run_id: str = "wfr_001",
    idempotency_hash: str = "a" * 64,
    status: WorkflowRunStatus = WorkflowRunStatus.PENDING,
) -> WorkflowRun:
    """构造仓储测试使用的WorkflowRun。"""
    started_at = NOW if status is not WorkflowRunStatus.PENDING else None
    ended_at = (
        NOW + timedelta(seconds=1)
        if status
        in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELED,
        }
        else None
    )
    return WorkflowRun(
        workflow_run_id=workflow_run_id,
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="user_001",
        idempotency_key_hash=idempotency_hash,
        request_hash="b" * 64,
        trace_id="trc_001",
        status=status,
        created_at=NOW,
        updated_at=ended_at or NOW,
        started_at=started_at,
        ended_at=ended_at,
        step_count=0,
    )


async def test_repository_saves_and_loads_by_both_business_keys(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    workflow_run = build_workflow_run()
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(workflow_run)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        by_key = await repository.get_by_idempotency_key_hash(
            "tenant_001",
            "a" * 64,
        )
        active = await repository.get_active_by_incident(
            "tenant_001",
            "inc_001",
        )

    assert by_key is not None
    assert active is not None
    assert by_key.workflow_run_id == "wfr_001"
    assert active.workflow_run_id == "wfr_001"


async def test_idempotency_key_unique_constraint_is_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        with pytest.raises(ConflictError):
            await repository.save(build_workflow_run(workflow_run_id="wfr_002"))
        await session.rollback()


async def test_only_one_active_workflow_is_allowed_per_incident(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        with pytest.raises(ConflictError):
            await repository.save(
                build_workflow_run(
                    workflow_run_id="wfr_002",
                    idempotency_hash="c" * 64,
                )
            )
        await session.rollback()


async def test_terminal_history_allows_a_new_active_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run(status=WorkflowRunStatus.SUCCEEDED))
        await repository.save(
            build_workflow_run(
                workflow_run_id="wfr_002",
                idempotency_hash="c" * 64,
            )
        )
        await session.commit()

    async with session_factory() as session:
        active = await SQLAlchemyWorkflowRunRepository(session).get_active_by_incident(
            "tenant_001", "inc_001"
        )

    assert active is not None
    assert active.workflow_run_id == "wfr_002"


async def test_repository_detects_stale_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await session.commit()

    async with session_factory() as first_session, session_factory() as second_session:
        first_repository = SQLAlchemyWorkflowRunRepository(first_session)
        second_repository = SQLAlchemyWorkflowRunRepository(second_session)
        first = await first_repository.get_active_by_incident(
            "tenant_001",
            "inc_001",
        )
        second = await second_repository.get_active_by_incident(
            "tenant_001",
            "inc_001",
        )
        assert first is not None and second is not None
        first.start(
            NOW + timedelta(seconds=1),
            lease_owner="worker_001",
            lease_expires_at=NOW + timedelta(minutes=2),
        )
        await first_repository.save(first)
        await first_session.commit()

        second.start(
            NOW + timedelta(seconds=2),
            lease_owner="worker_002",
            lease_expires_at=NOW + timedelta(minutes=2),
        )
        with pytest.raises(ConflictError, match="version conflict"):
            await second_repository.save(second)
        await second_session.rollback()


async def test_stored_invalid_workflow_is_mapped_to_persistence_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """历史脏工作流行不能把领域异常泄漏给应用层调用方。"""
    async with session_factory() as session:
        record = WorkflowRunMapper.to_record(build_workflow_run())
        record.trace_id = "trc_001\nforged"
        session.add(record)
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        with pytest.raises(
            PersistenceError,
            match="Stored workflow run violates the domain contract",
        ):
            await repository.get_by_id("tenant_001", "wfr_001")
        with pytest.raises(
            PersistenceError,
            match="Stored workflow run violates the domain contract",
        ):
            await repository.get_by_idempotency_key_hash(
                "tenant_001",
                "a" * 64,
            )
        with pytest.raises(
            PersistenceError,
            match="Stored workflow run violates the domain contract",
        ):
            await repository.get_active_by_incident(
                "tenant_001",
                "inc_001",
            )


async def test_workflow_lookup_rejects_control_character_keys(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """仓储端口直连查询也不能接受污染租户或工作流键。"""
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        with pytest.raises(AppValidationError, match="control characters"):
            await repository.get_by_id("tenant_001\nforged", "wfr_001")
        with pytest.raises(AppValidationError, match="control characters"):
            await repository.get_by_id("tenant_001", "wfr_001\x7fforged")
        with pytest.raises(AppValidationError, match="control characters"):
            await repository.get_active_by_incident(
                "tenant_001",
                "inc_001\tforged",
            )
        with pytest.raises(AppValidationError, match="control characters"):
            await repository.get_active_by_incident(
                "tenant_001\x7fforged",
                "inc_001",
            )


async def test_pending_workflow_is_claimed_with_execution_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """首次抢占应原子写入运行状态、租约和执行次数。"""
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await session.commit()

    claimed_at = NOW + timedelta(seconds=1)
    expires_at = NOW + timedelta(minutes=2)
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        claimed = await repository.claim_for_execution(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            worker_id="worker_001",
            now=claimed_at,
            lease_expires_at=expires_at,
        )
        await session.commit()

    assert claimed is not None
    assert claimed.status is WorkflowRunStatus.RUNNING
    assert claimed.started_at == claimed_at
    assert claimed.heartbeat_at == claimed_at
    assert claimed.lease_expires_at == expires_at
    assert claimed.lease_owner == "worker_001"
    assert claimed.execution_attempts == 1
    assert claimed.version == 2


async def test_unexpired_workflow_cannot_be_claimed_twice(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """未过期租约必须阻止重复消息启动第二个执行器。"""
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await repository.claim_for_execution(
            "tenant_001",
            "wfr_001",
            "worker_001",
            NOW + timedelta(seconds=1),
            NOW + timedelta(minutes=2),
        )
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        duplicate = await repository.claim_for_execution(
            "tenant_001",
            "wfr_001",
            "worker_002",
            NOW + timedelta(minutes=1),
            NOW + timedelta(minutes=3),
        )
        current = await repository.get_by_id("tenant_001", "wfr_001")

    assert duplicate is None
    assert current is not None
    assert current.lease_owner == "worker_001"
    assert current.execution_attempts == 1


async def test_expired_workflow_can_be_reclaimed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """执行器失联后允许接管，但保留首次开始时间并增加尝试次数。"""
    first_started_at = NOW + timedelta(seconds=1)
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await repository.claim_for_execution(
            "tenant_001",
            "wfr_001",
            "worker_001",
            first_started_at,
            NOW + timedelta(minutes=2),
        )
        await session.commit()

    reclaimed_at = NOW + timedelta(minutes=3)
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        reclaimed = await repository.claim_for_execution(
            "tenant_001",
            "wfr_001",
            "worker_002",
            reclaimed_at,
            NOW + timedelta(minutes=5),
        )
        await session.commit()

    assert reclaimed is not None
    assert reclaimed.started_at == first_started_at
    assert reclaimed.heartbeat_at == reclaimed_at
    assert reclaimed.lease_owner == "worker_002"
    assert reclaimed.execution_attempts == 2
    assert reclaimed.version == 3


async def test_terminal_or_cross_tenant_workflow_cannot_be_claimed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """终态任务和其他租户任务都不能获得执行租约。"""
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run(status=WorkflowRunStatus.SUCCEEDED))
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        terminal = await repository.claim_for_execution(
            "tenant_001",
            "wfr_001",
            "worker_001",
            NOW + timedelta(minutes=1),
            NOW + timedelta(minutes=3),
        )
        cross_tenant = await repository.claim_for_execution(
            "tenant_002",
            "wfr_001",
            "worker_001",
            NOW + timedelta(minutes=1),
            NOW + timedelta(minutes=3),
        )

    assert terminal is None
    assert cross_tenant is None


async def test_current_owner_can_atomically_renew_execution_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """续租应更新心跳、截止时间和版本，但不增加执行次数。"""
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await repository.claim_for_execution(
            "tenant_001",
            "wfr_001",
            "worker_001",
            NOW + timedelta(seconds=1),
            NOW + timedelta(minutes=2),
        )
        await session.commit()

    heartbeat_at = NOW + timedelta(minutes=1)
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        renewed = await repository.renew_execution_lease(
            "tenant_001",
            "wfr_001",
            "worker_001",
            heartbeat_at,
            NOW + timedelta(minutes=3),
        )
        await session.commit()

    assert renewed is not None
    assert renewed.heartbeat_at == heartbeat_at
    assert renewed.lease_expires_at == NOW + timedelta(minutes=3)
    assert renewed.execution_attempts == 1
    assert renewed.version == 3


@pytest.mark.parametrize(
    ("worker_id", "heartbeat_at", "new_expiry"),
    [
        (
            "worker_002",
            NOW + timedelta(minutes=1),
            NOW + timedelta(minutes=3),
        ),
        (
            "worker_001",
            NOW + timedelta(minutes=2),
            NOW + timedelta(minutes=4),
        ),
        (
            "worker_001",
            NOW,
            NOW + timedelta(minutes=3),
        ),
        (
            "worker_001",
            NOW + timedelta(minutes=1),
            NOW + timedelta(minutes=2),
        ),
    ],
)
async def test_invalid_heartbeat_cannot_change_execution_lease(
    session_factory: async_sessionmaker[AsyncSession],
    worker_id: str,
    heartbeat_at: datetime,
    new_expiry: datetime,
) -> None:
    """错误所有者、过期、倒退或未延长的心跳都不能更新记录。"""
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await repository.claim_for_execution(
            "tenant_001",
            "wfr_001",
            "worker_001",
            NOW + timedelta(seconds=1),
            NOW + timedelta(minutes=2),
        )
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        renewed = await repository.renew_execution_lease(
            "tenant_001",
            "wfr_001",
            worker_id,
            heartbeat_at,
            new_expiry,
        )
        current = await repository.get_by_id("tenant_001", "wfr_001")

    assert renewed is None
    assert current is not None
    assert current.lease_owner == "worker_001"
    assert current.heartbeat_at == NOW + timedelta(seconds=1)
    assert current.lease_expires_at == NOW + timedelta(minutes=2)
    assert current.version == 2


async def test_current_execution_attempt_can_complete_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """有效执行代次应原子写入终态、结束时间并释放租约。"""
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await repository.claim_for_execution(
            "tenant_001",
            "wfr_001",
            "worker_001",
            NOW + timedelta(seconds=1),
            NOW + timedelta(minutes=2),
        )
        await session.commit()

    completed_at = NOW + timedelta(minutes=1)
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        completed = await repository.complete_execution(
            "tenant_001",
            "wfr_001",
            "worker_001",
            1,
            WorkflowRunStatus.SUCCEEDED,
            completed_at,
        )
        await session.commit()

    assert completed is not None
    assert completed.status is WorkflowRunStatus.SUCCEEDED
    assert completed.ended_at == completed_at
    assert completed.lease_owner is None
    assert completed.lease_expires_at is None
    assert completed.heartbeat_at is None
    assert completed.execution_attempts == 1
    assert completed.version == 3


@pytest.mark.parametrize("operation", ["claim", "renew", "complete"])
async def test_execution_lease_operations_reject_newline_worker_id(
    session_factory: async_sessionmaker[AsyncSession],
    operation: str,
) -> None:
    """仓储直连也必须拒绝污染租约字段的执行者身份。"""
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        if operation != "claim":
            await repository.claim_for_execution(
                "tenant_001",
                "wfr_001",
                "worker_001",
                NOW + timedelta(seconds=1),
                NOW + timedelta(minutes=2),
            )
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        with pytest.raises(
            AppValidationError,
            match="worker_id",
        ):
            if operation == "claim":
                await repository.claim_for_execution(
                    "tenant_001",
                    "wfr_001",
                    "worker\n001",
                    NOW + timedelta(seconds=1),
                    NOW + timedelta(minutes=2),
                )
            elif operation == "renew":
                await repository.renew_execution_lease(
                    "tenant_001",
                    "wfr_001",
                    "worker\n001",
                    NOW + timedelta(minutes=1),
                    NOW + timedelta(minutes=3),
                )
            else:
                await repository.complete_execution(
                    "tenant_001",
                    "wfr_001",
                    "worker\n001",
                    1,
                    WorkflowRunStatus.SUCCEEDED,
                    NOW + timedelta(minutes=1),
                )

        current = await repository.get_by_id("tenant_001", "wfr_001")

    assert current is not None
    if operation == "claim":
        assert current.status is WorkflowRunStatus.PENDING
        assert current.lease_owner is None
        assert current.version == 1
    else:
        assert current.status is WorkflowRunStatus.RUNNING
        assert current.lease_owner == "worker_001"
        assert current.version == 2


@pytest.mark.parametrize(
    ("worker_id", "attempt", "completed_at"),
    [
        ("worker_002", 1, NOW + timedelta(minutes=1)),
        ("worker_001", 2, NOW + timedelta(minutes=1)),
        ("worker_001", 1, NOW + timedelta(minutes=2)),
        ("worker_001", 1, NOW),
    ],
)
async def test_stale_execution_cannot_complete_workflow(
    session_factory: async_sessionmaker[AsyncSession],
    worker_id: str,
    attempt: int,
    completed_at: datetime,
) -> None:
    """失权、旧代次、过期或倒退结果都不能覆盖运行状态。"""
    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        await repository.save(build_workflow_run())
        await repository.claim_for_execution(
            "tenant_001",
            "wfr_001",
            "worker_001",
            NOW + timedelta(seconds=1),
            NOW + timedelta(minutes=2),
        )
        await session.commit()

    async with session_factory() as session:
        repository = SQLAlchemyWorkflowRunRepository(session)
        completed = await repository.complete_execution(
            "tenant_001",
            "wfr_001",
            worker_id,
            attempt,
            WorkflowRunStatus.FAILED,
            completed_at,
        )
        current = await repository.get_by_id("tenant_001", "wfr_001")

    assert completed is None
    assert current is not None
    assert current.status is WorkflowRunStatus.RUNNING
    assert current.version == 2


def test_workflow_run_table_contains_control_plane_indexes() -> None:
    """取消幂等键必须有数据库唯一索引兜底。"""
    index_names = {index.name for index in WorkflowRunRecord.__table__.indexes}

    assert "uq_workflow_runs_tenant_cancellation_idempotency" in index_names
    assert "uq_workflow_runs_active_incident" in index_names
