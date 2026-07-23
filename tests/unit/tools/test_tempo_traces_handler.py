import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from devops_agent_platform.application.exceptions import TracesSourceError
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.observability import ObservabilityTarget
from devops_agent_platform.ports.traces import (
    TraceSearchResult,
    TraceSummary,
)
from devops_agent_platform.tools.executor import ToolExecutor
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.handlers.traces import (
    TempoTracesQueryHandler,
    TempoTracesQueryHandlerConfig,
    register_tempo_traces_tool,
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
    ) -> ObservabilityTarget:
        self.calls.append((tenant_id, incident_id))
        return ObservabilityTarget(
            tenant_id=tenant_id,
            incident_id=incident_id,
            service_name='checkout"api\\blue',
        )


class RecordingTraceSearch:
    """记录TraceQL并返回包含敏感名称的固定Trace摘要。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs) -> TraceSearchResult:
        self.calls.append(kwargs)
        return TraceSearchResult(
            traces=(
                TraceSummary(
                    trace_id="2f3e0cee77ae5dc9c17ade3689eb2e54",
                    root_service_name="checkout-api",
                    root_trace_name=(
                        "POST /checkout?token=private-token "
                        "alice@example.com\nretry"
                    ),
                    start_time_unix_nano="1782734400000000000",
                    duration_ms=557.5,
                    matched_spans=2,
                ),
                TraceSummary(
                    trace_id="3f3e0cee77ae5dc9c17ade3689eb2e55",
                    root_service_name="edge-" + ("服务" * 80),
                    root_trace_name="GET /catalog",
                    start_time_unix_nano="1782734399000000000",
                    duration_ms=1250.0,
                    matched_spans=1,
                ),
            ),
            inspected_traces=3100,
            inspected_bytes=3811736,
            possibly_truncated=True,
        )


def build_payload(**overrides) -> dict[str, Any]:
    """构造受控工作流注入的完整Traces Payload。"""
    payload: dict[str, Any] = {
        "tenant_id": "tenant_001",
        "incident_id": "inc_001",
        "window_minutes": 15,
        "limit": 50,
        "workflow_run_id": "wfr_001",
        "operator_id": "operator_001",
        "worker_id": "worker_001",
        "execution_attempt": 1,
        "trace_id": "trc_001",
        "plan_id": "default.rca",
        "plan_version": "v1",
        "step_id": "collect.traces",
    }
    payload.update(overrides)
    return payload


async def test_handler_uses_fixed_templates_and_sanitizes_output() -> None:
    """Handler应查询可信目标并对Trace名称做脱敏与截断。"""
    resolver = FixedTargetResolver()
    search = RecordingTraceSearch()
    handler = TempoTracesQueryHandler(
        resolver,
        search,
        config=TempoTracesQueryHandlerConfig(
            max_output_name_bytes=72,
        ),
        clock=lambda: NOW,
    )

    result = await handler.execute(build_payload(), "trc_001")

    assert resolver.calls == [("tenant_001", "inc_001")]
    assert len(search.calls) == 2
    assert result["target"]["service_name"] == 'checkout"api\\blue'
    assert result["effective_limit"] == 20
    assert result["possibly_truncated"] is True
    for call in search.calls:
        assert call["tenant_id"] == "tenant_001"
        assert call["limit"] == 10
        assert call["spans_per_span_set"] == 1
        assert call["trace_id"] == "trc_001"
        assert 'checkout\\"api\\\\blue' in call["query"]
        assert "most_recent=true" in call["query"]

    traces = result["signals"][0]["traces"]
    assert traces[0]["redacted"] is True
    assert "private-token" not in traces[0]["root_trace_name"]
    assert "alice@example.com" not in traces[0]["root_trace_name"]
    assert "\n" not in traces[0]["root_trace_name"]
    assert len(traces[1]["root_service_name"].encode()) <= 72
    assert traces[1]["truncated"] is True
    assert result["signals"][0]["search_cost"] == {
        "inspected_traces": 3100,
        "inspected_bytes": 3811736,
    }


async def test_signal_allowlist_controls_templates_not_raw_traceql() -> None:
    """调用方只能选择模板名称，任意TraceQL和阈值均被拒绝。"""
    search = RecordingTraceSearch()
    handler = TempoTracesQueryHandler(
        FixedTargetResolver(),
        search,
        clock=lambda: NOW,
    )

    result = await handler.execute(
        build_payload(signals=["errors"]),
        "trc_001",
    )

    assert [item["name"] for item in result["signals"]] == ["errors"]
    assert len(search.calls) == 1
    with pytest.raises(AppValidationError, match="unsupported fields"):
        await handler.execute(
            build_payload(query="{ true }", slow_threshold_ms=1),
            "trc_001",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_minutes": 0},
        {"window_minutes": 61},
        {"limit": 0},
        {"limit": 1},
        {"signals": []},
        {"signals": ["errors", "errors"]},
        {"signals": ["arbitrary_query"]},
    ],
)
async def test_invalid_query_controls_fail_before_network(
    overrides: dict[str, Any],
) -> None:
    """无界时间、容量和模板选择不能访问Tempo。"""
    search = RecordingTraceSearch()
    handler = TempoTracesQueryHandler(
        FixedTargetResolver(),
        search,
        clock=lambda: NOW,
    )

    with pytest.raises(AppValidationError):
        await handler.execute(build_payload(**overrides), "trc_001")

    assert search.calls == []


class FailingConcurrentSearch:
    """一个搜索失败时记录其余并发搜索是否被取消。"""

    def __init__(self) -> None:
        self.started = 0
        self.all_started = asyncio.Event()
        self.cancelled = 0

    async def search(self, **kwargs) -> TraceSearchResult:
        self.started += 1
        if self.started == 2:
            self.all_started.set()
        await self.all_started.wait()
        if "status = error" in kwargs["query"]:
            raise TracesSourceError("traces failed")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        raise AssertionError("unreachable")


async def test_one_signal_failure_cancels_sibling_searches() -> None:
    """任一模板失败后必须取消其余Tempo搜索。"""
    search = FailingConcurrentSearch()
    handler = TempoTracesQueryHandler(
        FixedTargetResolver(),
        search,
        clock=lambda: NOW,
    )

    with pytest.raises(TracesSourceError):
        await handler.execute(build_payload(), "trc_001")

    assert search.started == 2
    assert search.cancelled == 1


class BlockingTraceSearch:
    """阻塞全部搜索以验证外层取消传播。"""

    def __init__(self) -> None:
        self.started = 0
        self.all_started = asyncio.Event()
        self.cancelled = 0

    async def search(self, **kwargs) -> TraceSearchResult:
        del kwargs
        self.started += 1
        if self.started == 2:
            self.all_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        raise AssertionError("unreachable")


async def test_outer_cancellation_cancels_all_signal_searches() -> None:
    """工作流取消必须停止全部Trace模板搜索。"""
    search = BlockingTraceSearch()
    handler = TempoTracesQueryHandler(
        FixedTargetResolver(),
        search,
        clock=lambda: NOW,
    )
    task = asyncio.create_task(
        handler.execute(build_payload(), "trc_001")
    )
    await search.all_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert search.cancelled == 2


def test_registration_binds_exact_medium_risk_tool_contract() -> None:
    """注册助手必须绑定traces.query@v1及完整权限标签。"""
    registry = ToolHandlerRegistry()
    handler = TempoTracesQueryHandler(
        FixedTargetResolver(),
        RecordingTraceSearch(),
        clock=lambda: NOW,
    )

    definition = register_tempo_traces_tool(
        registry,
        handler,
        timeout_ms=4500,
    )

    registration = registry.get("traces.query", "v1")
    assert registration.handler is handler
    assert registration.definition is definition
    assert definition.risk_level is ToolRiskLevel.MEDIUM
    assert definition.timeout_ms == 4500
    assert definition.permission_tags == (
        "traces:read",
        "tenant:observe",
    )


async def test_registered_handler_runs_through_unified_executor() -> None:
    """真实注册定义应能通过统一执行器完成JSON结果校验。"""
    registry = ToolHandlerRegistry()
    search = RecordingTraceSearch()
    handler = TempoTracesQueryHandler(
        FixedTargetResolver(),
        search,
        clock=lambda: NOW,
    )
    definition = register_tempo_traces_tool(registry, handler)
    executor = ToolExecutor(registry)

    result = await executor.execute(
        definition,
        build_payload(signals=["slow_spans"]),
        "trc_001",
    )

    assert result["source"] == "tempo"
    assert result["signals"][0]["name"] == "slow_spans"
    assert len(search.calls) == 1


def test_handler_rejects_invalid_config_clock_and_collaborators() -> None:
    """错误配置、协作者和无时区时钟应在边界明确失败。"""
    with pytest.raises(AppValidationError):
        TempoTracesQueryHandler(
            object(),  # type: ignore[arg-type]
            RecordingTraceSearch(),
        )
    with pytest.raises(AppValidationError):
        TempoTracesQueryHandlerConfig(
            max_total_traces=50,
            max_requested_traces=40,
        )
    handler = TempoTracesQueryHandler(
        FixedTargetResolver(),
        RecordingTraceSearch(),
        clock=lambda: datetime(2026, 6, 29, 12, 0),
    )
    with pytest.raises(AppValidationError, match="timezone-aware"):
        asyncio.run(handler.execute(build_payload(), "trc_001"))
