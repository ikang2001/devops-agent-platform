from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import (
    ToolHandlerRegistration,
    ToolHandlerRegistry,
)


def build_definition(
    tool_name: str = "logs.query",
    version: str = "v1",
) -> ToolDefinition:
    """构造处理器注册测试使用的工具元数据。"""
    return ToolDefinition(
        tool_name=tool_name,
        version=version,
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=1000,
        permission_tags=("logs:read",),
    )


class SuccessfulHandler:
    """返回固定JSON对象的最小处理器替身。"""

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        return {"status": "ok"}


def test_register_and_get_exact_handler_version() -> None:
    """同名工具的不同版本必须路由到各自处理器。"""
    first = ToolHandlerRegistration(
        build_definition(version="v1"),
        SuccessfulHandler(),
    )
    second = ToolHandlerRegistration(
        build_definition(version="v2"),
        SuccessfulHandler(),
    )
    registry = ToolHandlerRegistry((second, first))

    assert registry.get("logs.query", "v1") is first
    assert registry.get("logs.query", "v2") is second
    assert registry.list_handlers() == [first, second]


def test_duplicate_handler_registration_never_overwrites() -> None:
    """重复键必须失败并保留第一次绑定。"""
    definition = build_definition()
    original = SuccessfulHandler()
    registry = ToolHandlerRegistry()
    registry.register(definition, original)

    with pytest.raises(ConflictError):
        registry.register(definition, SuccessfulHandler())

    assert registry.get("logs.query", "v1").handler is original


def test_unknown_handler_requires_explicit_failure() -> None:
    """未装配处理器时不能退化成假成功。"""
    registry = ToolHandlerRegistry()

    with pytest.raises(ResourceNotFound, match="metrics.query@v1"):
        registry.get("metrics.query", "v1")


def test_registration_rejects_object_without_execute_method() -> None:
    """启动装配阶段应尽早拦截不可执行对象。"""
    with pytest.raises(AppValidationError, match="execute"):
        ToolHandlerRegistration(
            build_definition(),
            object(),  # type: ignore[arg-type]
        )


def test_concurrent_duplicate_registration_has_single_winner() -> None:
    """并发注册同一版本时只能有一个成功者。"""
    registry = ToolHandlerRegistry()
    definition = build_definition()

    def register(_: int) -> bool:
        try:
            registry.register(definition, SuccessfulHandler())
        except ConflictError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(register, range(16)))

    assert results.count(True) == 1
    assert results.count(False) == 15
