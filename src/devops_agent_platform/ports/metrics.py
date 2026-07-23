from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from devops_agent_platform.ports.observability import (
    ObservabilityTarget,
    ObservabilityTargetResolverPort,
)

MetricsTarget = ObservabilityTarget
MetricsTargetResolverPort = ObservabilityTargetResolverPort


@dataclass(frozen=True)
class MetricSeries:
    """Prometheus矩阵结果中的一条不可变时间序列。"""

    labels: tuple[tuple[str, str], ...]
    samples: tuple[tuple[float, str], ...]


@dataclass(frozen=True)
class MetricsRangeResult:
    """一次范围查询的有界序列和服务端警告。"""

    series: tuple[MetricSeries, ...]
    warnings: tuple[str, ...]
    possibly_truncated: bool


class MetricsRangeQueryPort(Protocol):
    """执行一个受控Prometheus范围查询。"""

    async def query_range(
        self,
        *,
        tenant_id: str,
        query: str,
        start: datetime,
        end: datetime,
        step_seconds: int,
        series_limit: int,
        samples_per_series_limit: int,
        trace_id: str,
    ) -> MetricsRangeResult:
        """返回经过容量校验的矩阵结果。"""
        ...
