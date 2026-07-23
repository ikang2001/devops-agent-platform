import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from devops_agent_platform.application.exceptions import LogsSourceError
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.logs import LogsRangeResult, LogStream
from devops_agent_platform.ports.observability import ObservabilityTarget
from devops_agent_platform.tools.executor import ToolExecutor
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.handlers.logs import (
    LokiLogsQueryHandler,
    LokiLogsQueryHandlerConfig,
    register_loki_logs_tool,
)

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": "tenant_001\x7fforged"},
        {"incident_id": "inc_001\x7fforged"},
        {"service_name": "checkout-api\x7fforged"},
    ],
)
def test_observability_target_rejects_del_control_character(
    overrides: dict[str, object],
) -> None:
    """可观测目标会进入查询模板，不能携带不可见DEL。"""
    values = {
        "tenant_id": "tenant_001",
        "incident_id": "inc_001",
        "service_name": "checkout-api",
    }
    values.update(overrides)

    with pytest.raises(AppValidationError):
        ObservabilityTarget(**values)  # type: ignore[arg-type]


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


class RecordingRangeQuery:
    """记录LogQL并返回包含敏感信息的固定日志流。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def query_range(self, **kwargs) -> LogsRangeResult:
        self.calls.append(kwargs)
        return LogsRangeResult(
            streams=(
                LogStream(
                    labels=(
                        ("service", "checkout-api"),
                        ("pod", "checkout-api-7d9f"),
                        ("secret_label", "must-not-return"),
                    ),
                    entries=(
                        (
                            "1782734400000000000",
                            "password=hunter2 client_secret=private-value "
                            "Authorization: Bearer abcdefghijk "
                            "user=alice@example.com\nstack trace",
                        ),
                        (
                            "1782734399000000000",
                            "数据库连接失败" * 80,
                        ),
                    ),
                ),
            ),
            possibly_truncated=True,
        )


def build_payload(**overrides) -> dict[str, Any]:
    """构造受控工作流注入的完整Logs Payload。"""
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
        "step_id": "collect.logs",
    }
    payload.update(overrides)
    return payload


async def test_handler_uses_fixed_templates_and_sanitizes_output() -> None:
    """Handler应查询可信目标并对日志做白名单、脱敏和截断。"""
    resolver = FixedTargetResolver()
    query = RecordingRangeQuery()
    handler = LokiLogsQueryHandler(
        resolver,
        query,
        config=LokiLogsQueryHandlerConfig(max_output_line_bytes=96),
        clock=lambda: NOW,
    )

    result = await handler.execute(build_payload(), "trc_001")

    assert resolver.calls == [("tenant_001", "inc_001")]
    assert len(query.calls) == 3
    assert result["target"]["service_name"] == 'checkout"api\\blue'
    assert result["effective_limit"] == 39
    assert result["possibly_truncated"] is True
    for call in query.calls:
        assert call["tenant_id"] == "tenant_001"
        assert call["limit"] == 13
        assert call["trace_id"] == "trc_001"
        assert 'checkout\\"api\\\\blue' in call["query"]

    entries = result["signals"][0]["entries"]
    assert entries[0]["labels"] == {
        "service": "checkout-api",
        "pod": "checkout-api-7d9f",
    }
    assert entries[0]["redacted"] is True
    assert "hunter2" not in entries[0]["line"]
    assert "private-value" not in entries[0]["line"]
    assert "abcdefghijk" not in entries[0]["line"]
    assert "alice@example.com" not in entries[0]["line"]
    assert "\n" not in entries[0]["line"]
    assert len(entries[1]["line"].encode()) <= 96
    assert entries[1]["truncated"] is True


async def test_signal_allowlist_controls_templates_not_raw_logql() -> None:
    """调用方只能选择模板名称，未知字段和任意LogQL均被拒绝。"""
    query = RecordingRangeQuery()
    handler = LokiLogsQueryHandler(
        FixedTargetResolver(),
        query,
        clock=lambda: NOW,
    )

    result = await handler.execute(
        build_payload(signals=["errors", "timeouts"]),
        "trc_001",
    )

    assert [item["name"] for item in result["signals"]] == [
        "errors",
        "timeouts",
    ]
    assert len(query.calls) == 2
    with pytest.raises(AppValidationError, match="unsupported fields"):
        await handler.execute(
            build_payload(query='{service=~".+"}'),
            "trc_001",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_minutes": 0},
        {"window_minutes": 61},
        {"limit": 0},
        {"limit": 2},
        {"signals": []},
        {"signals": ["errors", "errors"]},
        {"signals": ["arbitrary_query"]},
    ],
)
async def test_invalid_query_controls_fail_before_network(
    overrides: dict[str, Any],
) -> None:
    """无界时间、容量和模板选择不能访问Loki。"""
    query = RecordingRangeQuery()
    handler = LokiLogsQueryHandler(
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

    async def query_range(self, **kwargs) -> LogsRangeResult:
        self.started += 1
        if self.started == 3:
            self.all_started.set()
        await self.all_started.wait()
        if "timeout" in kwargs["query"]:
            raise LogsSourceError("logs failed")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        raise AssertionError("unreachable")


async def test_one_signal_failure_cancels_sibling_queries() -> None:
    """任一模板失败后必须取消其余Loki请求。"""
    query = FailingConcurrentQuery()
    handler = LokiLogsQueryHandler(
        FixedTargetResolver(),
        query,
        clock=lambda: NOW,
    )

    with pytest.raises(LogsSourceError):
        await handler.execute(build_payload(), "trc_001")

    assert query.started == 3
    assert query.cancelled == 2


class BlockingRangeQuery:
    """阻塞全部查询以验证外层取消传播。"""

    def __init__(self) -> None:
        self.started = 0
        self.all_started = asyncio.Event()
        self.cancelled = 0

    async def query_range(self, **kwargs) -> LogsRangeResult:
        del kwargs
        self.started += 1
        if self.started == 3:
            self.all_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        raise AssertionError("unreachable")


async def test_outer_cancellation_cancels_all_signal_queries() -> None:
    """工作流取消必须停止全部日志模板查询。"""
    query = BlockingRangeQuery()
    handler = LokiLogsQueryHandler(
        FixedTargetResolver(),
        query,
        clock=lambda: NOW,
    )
    task = asyncio.create_task(handler.execute(build_payload(), "trc_001"))
    await query.all_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert query.cancelled == 3


def test_registration_binds_exact_medium_risk_tool_contract() -> None:
    """注册助手必须绑定logs.query@v1及完整权限标签。"""
    registry = ToolHandlerRegistry()
    handler = LokiLogsQueryHandler(
        FixedTargetResolver(),
        RecordingRangeQuery(),
        clock=lambda: NOW,
    )

    definition = register_loki_logs_tool(
        registry,
        handler,
        timeout_ms=4000,
    )

    registration = registry.get("logs.query", "v1")
    assert registration.handler is handler
    assert registration.definition is definition
    assert definition.risk_level is ToolRiskLevel.MEDIUM
    assert definition.timeout_ms == 4000
    assert definition.permission_tags == (
        "logs:read",
        "tenant:observe",
    )


async def test_registered_handler_runs_through_unified_executor() -> None:
    """真实注册定义应能通过统一执行器完成JSON结果校验。"""
    registry = ToolHandlerRegistry()
    query = RecordingRangeQuery()
    handler = LokiLogsQueryHandler(
        FixedTargetResolver(),
        query,
        clock=lambda: NOW,
    )
    definition = register_loki_logs_tool(registry, handler)
    executor = ToolExecutor(registry)

    result = await executor.execute(
        definition,
        build_payload(signals=["errors"]),
        "trc_001",
    )

    assert result["source"] == "loki"
    assert result["signals"][0]["name"] == "errors"
    assert len(query.calls) == 1


def test_handler_rejects_invalid_config_clock_and_collaborators() -> None:
    """错误配置、协作者和无时区时钟应在边界明确失败。"""
    with pytest.raises(AppValidationError):
        LokiLogsQueryHandler(
            object(),  # type: ignore[arg-type]
            RecordingRangeQuery(),
        )
    with pytest.raises(AppValidationError):
        LokiLogsQueryHandlerConfig(
            max_total_entries=50,
            max_requested_entries=40,
        )
    handler = LokiLogsQueryHandler(
        FixedTargetResolver(),
        RecordingRangeQuery(),
        clock=lambda: datetime(2026, 6, 29, 12, 0),
    )
    with pytest.raises(AppValidationError, match="timezone-aware"):
        asyncio.run(handler.execute(build_payload(), "trc_001"))


def test_utf8_truncation_never_exceeds_tiny_limit() -> None:
    """极小上限也不能被截断标记自身突破。"""
    value, truncated = LokiLogsQueryHandler._truncate_utf8("敏感日志", 4)

    assert truncated is True
    assert len(value.encode()) <= 4
