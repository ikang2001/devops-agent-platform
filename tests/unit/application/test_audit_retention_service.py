from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.services.audit_retention_service import (
    AuditRetentionConfig,
    AuditRetentionService,
)
from devops_agent_platform.domain.exceptions import AppValidationError

NOW = datetime(2026, 6, 30, 14, 0, tzinfo=UTC)


class RecordingRetentionStore:
    """记录清理参数并返回可配置结果。"""

    def __init__(self, result: int = 2) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def purge_batch(self, **kwargs) -> int:
        self.calls.append(kwargs)
        return self.result


async def test_service_calculates_cutoff_and_forwards_batch_limit() -> None:
    """应用服务只负责时间策略，不感知 SQLAlchemy。"""
    store = RecordingRetentionStore()
    service = AuditRetentionService(
        store,
        AuditRetentionConfig(
            retention_period=timedelta(days=30),
            batch_size=20,
        ),
        clock=lambda: NOW,
    )

    result = await service.purge_once()

    assert result.cutoff == NOW - timedelta(days=30)
    assert result.purged_at == NOW
    assert result.purged_workflow_runs == 2
    assert store.calls == [
        {
            "cutoff": NOW - timedelta(days=30),
            "purged_at": NOW,
            "limit": 20,
        }
    ]


@pytest.mark.parametrize(
    "config",
    [
        {"retention_period": timedelta(0)},
        {"retention_period": timedelta(days=3651)},
        {"batch_size": 0},
        {"batch_size": True},
        {"batch_size": 1001},
    ],
)
def test_config_rejects_unsafe_values(config: dict) -> None:
    """留存期和批次不能配置为无界或无效值。"""
    with pytest.raises(AppValidationError):
        AuditRetentionConfig(**config)


async def test_service_rejects_naive_clock() -> None:
    """无时区时钟不能参与数据删除边界计算。"""
    service = AuditRetentionService(
        RecordingRetentionStore(),
        clock=lambda: NOW.replace(tzinfo=None),
    )

    with pytest.raises(AppValidationError, match="timezone-aware"):
        await service.purge_once()


@pytest.mark.parametrize("invalid_count", [-1, 101, True, "1"])
async def test_service_rejects_invalid_store_count(
    invalid_count: object,
) -> None:
    """基础设施返回值异常时不能伪造成功清理结果。"""
    store = RecordingRetentionStore()
    store.result = invalid_count  # type: ignore[assignment]
    service = AuditRetentionService(
        store,
        AuditRetentionConfig(batch_size=100),
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="invalid count"):
        await service.purge_once()
