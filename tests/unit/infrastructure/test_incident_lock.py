from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import DBAPIError

from devops_agent_platform.application.exceptions import (
    PersistenceError,
    ResourceBusyError,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.adapters.sqlalchemy.incident_lock import (
    PostgreSQLIncidentCorrelationLock,
)


def build_session(dialect_name: str = "postgresql") -> Mock:
    """构造只暴露锁适配器所需行为的 AsyncSession 替身。"""
    session = Mock()
    session.execute = AsyncMock()
    session.get_bind.return_value = SimpleNamespace(
        dialect=SimpleNamespace(name=dialect_name)
    )
    return session


async def test_acquire_sets_local_timeout_then_obtains_transaction_lock() -> None:
    session = build_session()
    lock = PostgreSQLIncidentCorrelationLock(session, timeout_ms=1500)

    await lock.acquire("tenant_001", "checkout-api")

    assert session.execute.await_count == 3
    timeout_call, lock_call, restore_call = session.execute.await_args_list
    assert "set_config" in str(timeout_call.args[0])
    assert timeout_call.args[1] == {"lock_timeout": "1500ms"}
    assert "pg_advisory_xact_lock" in str(lock_call.args[0])
    assert isinstance(lock_call.args[1]["lock_key"], int)
    assert "'0'" in str(restore_call.args[0])


def test_lock_key_is_stable_and_scoped_by_tenant_and_service() -> None:
    first = PostgreSQLIncidentCorrelationLock._build_lock_key(
        "tenant_001",
        "checkout-api",
    )
    repeated = PostgreSQLIncidentCorrelationLock._build_lock_key(
        "tenant_001",
        "checkout-api",
    )
    another_tenant = PostgreSQLIncidentCorrelationLock._build_lock_key(
        "tenant_002",
        "checkout-api",
    )
    another_service = PostgreSQLIncidentCorrelationLock._build_lock_key(
        "tenant_001",
        "payments-api",
    )

    assert first == repeated
    assert first != another_tenant
    assert first != another_service
    assert -(2**63) <= first < 2**63


async def test_non_postgresql_database_is_not_silently_unlocked() -> None:
    lock = PostgreSQLIncidentCorrelationLock(build_session("sqlite"))

    with pytest.raises(PersistenceError, match="requires PostgreSQL"):
        await lock.acquire("tenant_001", "checkout-api")


class LockTimeoutDriverError(Exception):
    """模拟 PostgreSQL lock_not_available SQLSTATE。"""

    sqlstate = "55P03"


async def test_lock_timeout_is_mapped_to_resource_busy() -> None:
    session = build_session()
    driver_error = LockTimeoutDriverError("lock timeout")
    session.execute.side_effect = [
        None,
        DBAPIError("lock sql", {}, driver_error, False),
    ]
    lock = PostgreSQLIncidentCorrelationLock(session)

    with pytest.raises(ResourceBusyError) as exc_info:
        await lock.acquire("tenant_001", "checkout-api")

    assert exc_info.value.code == "RESOURCE_BUSY"


@pytest.mark.parametrize(
    ("tenant_id", "service_name", "timeout_ms"),
    [
        ("", "checkout-api", 2000),
        (" tenant_001", "checkout-api", 2000),
        ("tenant_001", "", 2000),
        ("tenant_001", "checkout-api ", 2000),
        ("tenant_001", "checkout-api", 0),
        ("tenant_001", "checkout-api", 30_001),
        ("tenant_001", "checkout-api", True),
    ],
)
async def test_invalid_lock_parameters_are_rejected(
    tenant_id: str,
    service_name: str,
    timeout_ms: int,
) -> None:
    with pytest.raises(AppValidationError):
        lock = PostgreSQLIncidentCorrelationLock(
            build_session(),
            timeout_ms=timeout_ms,
        )
        await lock.acquire(tenant_id, service_name)
