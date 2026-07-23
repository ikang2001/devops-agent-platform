from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.audit_retention import (
    AuditRetentionStorePort,
)

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class AuditRetentionConfig:
    """RCA 审计数据留存周期和单批容量配置。"""

    retention_period: timedelta = timedelta(days=30)
    batch_size: int = 100

    def __post_init__(self) -> None:
        """拒绝非正留存期和可能形成长事务的超大批次。"""
        if (
            not isinstance(self.retention_period, timedelta)
            or self.retention_period <= timedelta(0)
            or self.retention_period > timedelta(days=3650)
        ):
            raise AppValidationError(
                "retention_period must be between 0 and 3650 days"
            )
        if (
            isinstance(self.batch_size, bool)
            or not isinstance(self.batch_size, int)
            or not 1 <= self.batch_size <= 1000
        ):
            raise AppValidationError("batch_size must be between 1 and 1000")


@dataclass(frozen=True)
class AuditRetentionResult:
    """单次清理执行的可观测结果。"""

    cutoff: datetime
    purged_at: datetime
    purged_workflow_runs: int


class AuditRetentionService:
    """计算留存截止时间并执行一个有限清理批次。"""

    def __init__(
        self,
        store: AuditRetentionStorePort,
        config: AuditRetentionConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._config = config or AuditRetentionConfig()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def purge_once(self) -> AuditRetentionResult:
        """清理一个批次；调度循环由后续 Worker 增量负责。"""
        purged_at = self._now()
        cutoff = purged_at - self._config.retention_period
        purged_count = await self._store.purge_batch(
            cutoff=cutoff,
            purged_at=purged_at,
            limit=self._config.batch_size,
        )
        if (
            isinstance(purged_count, bool)
            or not isinstance(purged_count, int)
            or not 0 <= purged_count <= self._config.batch_size
        ):
            raise RuntimeError("Audit retention store returned an invalid count")
        return AuditRetentionResult(
            cutoff=cutoff,
            purged_at=purged_at,
            purged_workflow_runs=purged_count,
        )

    def _now(self) -> datetime:
        """读取带时区时钟并统一转换为 UTC。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)
