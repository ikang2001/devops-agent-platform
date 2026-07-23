import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from devops_agent_platform.application.exceptions import (
    ToolExecutionError,
    ToolExecutionTimeoutError,
)
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.executor import (
    ToolExecutor,
    ToolExecutorConfig,
)
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry


def build_definition(
    *,
    timeout_ms: int = 1000,
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
) -> ToolDefinition:
    """构造统一执行器测试使用的工具定义。"""
    return ToolDefinition(
        tool_name="logs.query",
        version="v1",
        risk_level=risk_level,
        timeout_ms=timeout_ms,
        permission_tags=("logs:read",),
    )


class RecordingHandler:
    """记录输入并返回可配置结果的处理器替身。"""

    def __init__(self, result: object | None = None) -> None:
        self.result = result if result is not None else {"status": "ok"}
        self.payload: Mapping[str, Any] | None = None
        self.trace_id: str | None = None

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        self.payload = payload
        self.trace_id = trace_id
        return self.result  # type: ignore[return-value]


class BlockingHandler:
    """保持阻塞以验证超时和外部取消传播。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return {"status": "unreachable"}


class FailingHandler:
    """抛出含敏感文本的异常，验证统一错误脱敏。"""

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        raise RuntimeError("secret-token=do-not-leak")


class ConcurrencyHandler:
    """记录并发峰值，并由测试控制释放时机。"""

    def __init__(self, expected_parallelism: int) -> None:
        self.expected_parallelism = expected_parallelism
        self.active = 0
        self.maximum_active = 0
        self.limit_reached = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.active == self.expected_parallelism:
            self.limit_reached.set()
        try:
            await self.release.wait()
            return {"status": "ok"}
        finally:
            self.active -= 1


def build_executor(
    definition: ToolDefinition,
    handler: object,
    *,
    config: ToolExecutorConfig | None = None,
) -> ToolExecutor:
    """装配单工具执行器，保持测试关注点集中。"""
    registry = ToolHandlerRegistry()
    registry.register(definition, handler)  # type: ignore[arg-type]
    return ToolExecutor(registry, config)


async def test_execute_routes_exact_version_and_copies_json_data() -> None:
    """执行器应路由精确版本，并隔离调用方输入与处理器输出。"""
    definition = build_definition()
    source_result = {"items": [{"message": "ok"}]}
    handler = RecordingHandler(source_result)
    executor = build_executor(definition, handler)
    source_payload = {"filters": {"service": "api"}}

    result = await executor.execute(
        definition,
        source_payload,
        "trc_001",
    )

    assert result == source_result
    assert result is not source_result
    assert handler.payload == source_payload
    assert handler.payload is not source_payload
    assert handler.trace_id == "trc_001"


async def test_missing_handler_fails_without_business_success() -> None:
    """仅注册元数据但没有处理器时必须明确失败。"""
    executor = ToolExecutor(ToolHandlerRegistry())

    with pytest.raises(ResourceNotFound, match="logs.query@v1"):
        await executor.execute(
            build_definition(),
            {"query": "error"},
            "trc_001",
        )


async def test_definition_drift_is_rejected_before_handler_execution() -> None:
    """相同键的风险或超时定义漂移时不得调用处理器。"""
    registered = build_definition()
    requested = build_definition(risk_level=ToolRiskLevel.MEDIUM)
    handler = RecordingHandler()
    executor = build_executor(registered, handler)

    with pytest.raises(ToolExecutionError, match="definition mismatch"):
        await executor.execute(requested, {}, "trc_001")

    assert handler.payload is None


async def test_timeout_cancels_running_handler() -> None:
    """工具超时后必须取消底层协程并返回稳定超时异常。"""
    definition = build_definition(timeout_ms=20)
    handler = BlockingHandler()
    executor = build_executor(definition, handler)

    with pytest.raises(ToolExecutionTimeoutError, match="logs.query@v1"):
        await executor.execute(definition, {}, "trc_001")

    assert handler.cancelled is True


async def test_outer_cancellation_is_not_wrapped() -> None:
    """租约丢失等外部取消信号必须原样传播。"""
    definition = build_definition()
    handler = BlockingHandler()
    executor = build_executor(definition, handler)
    task = asyncio.create_task(
        executor.execute(definition, {}, "trc_001")
    )
    await handler.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert handler.cancelled is True


async def test_handler_error_is_wrapped_without_leaking_message() -> None:
    """第三方异常文本不得进入对外稳定错误消息。"""
    definition = build_definition()
    executor = build_executor(definition, FailingHandler())

    with pytest.raises(ToolExecutionError) as exc_info:
        await executor.execute(definition, {}, "trc_001")

    assert "logs.query@v1" in str(exc_info.value)
    assert "secret-token" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


@pytest.mark.parametrize(
    "result",
    [
        ["not", "an", "object"],
        {"value": float("nan")},
        {"value": object()},
    ],
)
async def test_invalid_handler_output_is_rejected(result: object) -> None:
    """非对象、NaN和不可序列化输出都不能进入Agent上下文。"""
    definition = build_definition()
    executor = build_executor(
        definition,
        RecordingHandler(result),
    )

    with pytest.raises(ToolExecutionError):
        await executor.execute(definition, {}, "trc_001")


async def test_oversized_input_and_output_are_rejected() -> None:
    """输入输出都必须受独立字节上限保护。"""
    definition = build_definition()
    executor = build_executor(
        definition,
        RecordingHandler({"value": "x" * 64}),
        config=ToolExecutorConfig(
            max_input_bytes=32,
            max_output_bytes=32,
        ),
    )

    with pytest.raises(AppValidationError, match="payload exceeds"):
        await executor.execute(
            definition,
            {"value": "x" * 64},
            "trc_001",
        )
    with pytest.raises(ToolExecutionError, match="result exceeds"):
        await executor.execute(definition, {}, "trc_001")


async def test_global_concurrency_limit_bounds_parallel_handlers() -> None:
    """并发请求超过上限时，其余调用必须等待执行槽位。"""
    definition = build_definition()
    handler = ConcurrencyHandler(expected_parallelism=2)
    executor = build_executor(
        definition,
        handler,
        config=ToolExecutorConfig(max_concurrency=2),
    )
    tasks = [
        asyncio.create_task(
            executor.execute(definition, {"index": index}, "trc_001")
        )
        for index in range(3)
    ]

    await asyncio.wait_for(handler.limit_reached.wait(), timeout=1)
    await asyncio.sleep(0)
    assert handler.maximum_active == 2

    handler.release.set()
    await asyncio.gather(*tasks)
    assert handler.maximum_active == 2


@pytest.mark.parametrize(
    "trace_id",
    ["", " trc_001", "trc_001 ", "x" * 129],
)
async def test_invalid_trace_id_is_rejected(trace_id: str) -> None:
    """链路标识为空、超长或含首尾空白时不得执行工具。"""
    definition = build_definition()
    handler = RecordingHandler()
    executor = build_executor(definition, handler)

    with pytest.raises(AppValidationError, match="trace_id"):
        await executor.execute(definition, {}, trace_id)

    assert handler.payload is None
