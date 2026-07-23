import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from devops_agent_platform.application.exceptions import MetricsSourceError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.metrics import (
    MetricSeries,
    MetricsRangeResult,
    MetricsTarget,
)
from devops_agent_platform.tools.executor import ToolExecutor
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.handlers.metrics import (
    PrometheusMetricsQueryHandler,
    register_prometheus_metrics_tool,
)

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


class FixedTargetResolver:
    """返回可信固定服务目标并记录查询身份。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def resolve(
        self,
        tenant_id: str,
        incident_id: str,
    ) -> MetricsTarget:
        self.calls.append((tenant_id, incident_id))
        return MetricsTarget(
            tenant_id=tenant_id,
            incident_id=incident_id,
            service_name='checkout"api\\blue',
        )


class RecordingRangeQuery:
    """记录PromQL请求并返回包含非有限值的固定矩阵。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def query_range(self, **kwargs) -> MetricsRangeResult:
        self.calls.append(kwargs)
        return MetricsRangeResult(
            series=(
                MetricSeries(
                    labels=(
                        ("service", "checkout-api"),
                        ("secret_label", "must-not-return"),
                    ),
                    samples=(
                        (NOW.timestamp() - 30, "1"),
                        (NOW.timestamp() - 15, "NaN"),
                        (NOW.timestamp(), "3"),
                    ),
                ),
            ),
            warnings=("partial data",),
            possibly_truncated=False,
        )


def build_payload(**overrides) -> dict[str, Any]:
    """构造受控工作流注入的完整Metrics Payload。"""
    payload: dict[str, Any] = {
        "tenant_id": "tenant_001",
        "incident_id": "inc_001",
        "window_minutes": 15,
        "max_series": 200,
        "workflow_run_id": "wfr_001",
        "operator_id": "operator_001",
        "worker_id": "worker_001",
        "execution_attempt": 1,
        "trace_id": "trc_001",
        "plan_id": "default.rca",
        "plan_version": "v1",
        "step_id": "collect.metrics",
    }
    payload.update(overrides)
    return payload


async def test_handler_uses_fixed_templates_and_returns_bounded_summary() -> None:
    """Handler应解析可信目标、执行黄金信号并压缩样本。"""
    resolver = FixedTargetResolver()
    query = RecordingRangeQuery()
    handler = PrometheusMetricsQueryHandler(
        resolver,
        query,
        clock=lambda: NOW,
    )

    result = await handler.execute(build_payload(), "trc_001")

    assert resolver.calls == [("tenant_001", "inc_001")]
    assert len(query.calls) == 4
    assert result["target"]["service_name"] == 'checkout"api\\blue'
    assert result["window"]["step_seconds"] == 15
    assert result["effective_max_series"] == 48
    assert result["possibly_truncated"] is False
    for call in query.calls:
        assert call["tenant_id"] == "tenant_001"
        assert call["series_limit"] == 12
        assert call["trace_id"] == "trc_001"
        assert 'checkout\\"api\\\\blue' in call["query"]

    summary = result["signals"][0]["series"][0]
    assert summary["labels"] == {"service": "checkout-api"}
    assert summary["sample_count"] == 3
    assert summary["finite_sample_count"] == 2
    assert summary["non_finite_sample_count"] == 1
    assert summary["first"]["value"] == 1.0
    assert summary["latest"]["value"] == 3.0
    assert summary["minimum"] == 1.0
    assert summary["maximum"] == 3.0
    assert summary["average"] == 2.0
    assert summary["change"] == 2.0


async def test_signal_allowlist_controls_templates_not_raw_promql() -> None:
    """调用方只能选择模板名称，未知字段和任意PromQL均被拒绝。"""
    query = RecordingRangeQuery()
    handler = PrometheusMetricsQueryHandler(
        FixedTargetResolver(),
        query,
        clock=lambda: NOW,
    )

    result = await handler.execute(
        build_payload(signals=["availability", "error_ratio"]),
        "trc_001",
    )

    assert [item["name"] for item in result["signals"]] == [
        "availability",
        "error_ratio",
    ]
    assert len(query.calls) == 2
    with pytest.raises(AppValidationError, match="unsupported fields"):
        await handler.execute(
            build_payload(query='up{job="attacker"}'),
            "trc_001",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_minutes": 0},
        {"window_minutes": 61},
        {"max_series": 0},
        {"max_series": 3},
        {"signals": []},
        {"signals": ["availability", "availability"]},
        {"signals": ["arbitrary_query"]},
    ],
)
async def test_invalid_query_controls_fail_before_network(
    overrides: dict[str, Any],
) -> None:
    """无界时间、容量和模板选择不能访问Prometheus。"""
    query = RecordingRangeQuery()
    handler = PrometheusMetricsQueryHandler(
        FixedTargetResolver(),
        query,
        clock=lambda: NOW,
    )

    with pytest.raises(AppValidationError):
        await handler.execute(build_payload(**overrides), "trc_001")

    assert query.calls == []


class FailingConcurrentQuery:
    """一个查询失败时记录其余并发查询是否被取消。"""

    def __init__(self) -> None:
        self.started = 0
        self.all_started = asyncio.Event()
        self.cancelled = 0

    async def query_range(self, **kwargs) -> MetricsRangeResult:
        self.started += 1
        if self.started == 4:
            self.all_started.set()
        await self.all_started.wait()
        if "avg by" in kwargs["query"]:
            raise MetricsSourceError("metrics failed")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        raise AssertionError("unreachable")


async def test_one_signal_failure_cancels_sibling_queries() -> None:
    """任一模板失败后必须取消其余Prometheus请求。"""
    query = FailingConcurrentQuery()
    handler = PrometheusMetricsQueryHandler(
        FixedTargetResolver(),
        query,
        clock=lambda: NOW,
    )

    with pytest.raises(MetricsSourceError):
        await handler.execute(build_payload(), "trc_001")

    assert query.started == 4
    assert query.cancelled == 3


class BlockingRangeQuery:
    """阻塞全部查询以验证外层取消传播。"""

    def __init__(self) -> None:
        self.started = 0
        self.all_started = asyncio.Event()
        self.cancelled = 0

    async def query_range(self, **kwargs) -> MetricsRangeResult:
        del kwargs
        self.started += 1
        if self.started == 4:
            self.all_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        raise AssertionError("unreachable")


async def test_outer_cancellation_cancels_all_signal_queries() -> None:
    """工作流取消必须停止全部黄金信号查询。"""
    query = BlockingRangeQuery()
    handler = PrometheusMetricsQueryHandler(
        FixedTargetResolver(),
        query,
        clock=lambda: NOW,
    )
    task = asyncio.create_task(
        handler.execute(build_payload(), "trc_001")
    )
    await query.all_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert query.cancelled == 4


def test_registration_binds_exact_low_risk_tool_contract() -> None:
    """注册助手必须绑定metrics.query@v1及完整权限标签。"""
    registry = ToolHandlerRegistry()
    handler = PrometheusMetricsQueryHandler(
        FixedTargetResolver(),
        RecordingRangeQuery(),
        clock=lambda: NOW,
    )

    definition = register_prometheus_metrics_tool(
        registry,
        handler,
        timeout_ms=4000,
    )

    registration = registry.get("metrics.query", "v1")
    assert registration.handler is handler
    assert registration.definition is definition
    assert definition.timeout_ms == 4000
    assert definition.permission_tags == (
        "metrics:read",
        "tenant:observe",
    )


async def test_registered_handler_runs_through_unified_executor() -> None:
    """真实注册定义应能通过统一执行器完成JSON结果校验。"""
    registry = ToolHandlerRegistry()
    query = RecordingRangeQuery()
    handler = PrometheusMetricsQueryHandler(
        FixedTargetResolver(),
        query,
        clock=lambda: NOW,
    )
    definition = register_prometheus_metrics_tool(registry, handler)
    executor = ToolExecutor(registry)

    result = await executor.execute(
        definition,
        build_payload(signals=["availability"]),
        "trc_001",
    )

    assert result["source"] == "prometheus"
    assert result["signals"][0]["name"] == "availability"
    assert len(query.calls) == 1


def test_handler_rejects_invalid_clock_and_collaborators() -> None:
    """错误协作者和无时区时钟在执行边界明确失败。"""
    with pytest.raises(AppValidationError):
        PrometheusMetricsQueryHandler(
            object(),  # type: ignore[arg-type]
            RecordingRangeQuery(),
        )
    handler = PrometheusMetricsQueryHandler(
        FixedTargetResolver(),
        RecordingRangeQuery(),
        clock=lambda: datetime(2026, 6, 29, 12, 0),
    )
    with pytest.raises(AppValidationError, match="timezone-aware"):
        asyncio.run(handler.execute(build_payload(), "trc_001"))
