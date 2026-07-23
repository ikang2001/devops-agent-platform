import asyncio
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from devops_agent_platform.application.exceptions import (
    ToolExecutionError,
    ToolExecutionTimeoutError,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry


@dataclass(frozen=True)
class ToolExecutorConfig:
    """统一工具执行器的容量与并发保护参数。"""

    max_input_bytes: int = 64 * 1024
    max_output_bytes: int = 64 * 1024
    max_concurrency: int = 32

    def __post_init__(self) -> None:
        """限制配置上界，避免错误配置把保护机制变成无界资源。"""
        self._validate_positive_int(
            "max_input_bytes",
            self.max_input_bytes,
            1024 * 1024,
        )
        self._validate_positive_int(
            "max_output_bytes",
            self.max_output_bytes,
            1024 * 1024,
        )
        self._validate_positive_int(
            "max_concurrency",
            self.max_concurrency,
            1024,
        )

    @staticmethod
    def _validate_positive_int(
        field_name: str,
        value: int,
        maximum: int,
    ) -> None:
        """拒绝布尔值、非整数、零值和不合理上界。"""
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise AppValidationError(
                f"{field_name} must be between 1 and {maximum}"
            )


class ToolExecutor:
    """统一执行已注册工具，并实施超时、容量和并发保护。"""

    def __init__(
        self,
        registry: ToolHandlerRegistry,
        config: ToolExecutorConfig | None = None,
    ) -> None:
        """创建执行器；注册表由启动装配显式提供，不内置假处理器。"""
        if not isinstance(registry, ToolHandlerRegistry):
            raise AppValidationError(
                "registry must be a ToolHandlerRegistry"
            )
        self._registry = registry
        self._config = config or ToolExecutorConfig()
        self._semaphore = asyncio.Semaphore(
            self._config.max_concurrency
        )

    async def execute(
        self,
        definition: ToolDefinition,
        payload: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        """执行精确版本处理器，并返回经过JSON归一化的结果。

        调用约束：
            权限与风险校验由上层受控工作流在全计划预检阶段完成。
            超时包含等待并发槽位的时间，防止请求在队列中无限滞留。

        异常：
            AppValidationError: 输入或trace_id不符合约束。
            ResourceNotFound: 工具处理器尚未装配。
            ToolExecutionTimeoutError: 等待或执行超过工具定义时限。
            ToolExecutionError: 定义漂移、处理器失败或输出违反契约。
        """
        if not isinstance(definition, ToolDefinition):
            raise AppValidationError(
                "definition must be a ToolDefinition"
            )
        self._validate_trace_id(trace_id)
        normalized_payload = self._normalize_input(payload)
        registration = self._registry.get(
            definition.tool_name,
            definition.version,
        )
        if registration.definition != definition:
            raise ToolExecutionError(
                "Tool definition mismatch: "
                f"{definition.tool_name}@{definition.version}"
            )

        try:
            async with asyncio.timeout(definition.timeout_ms / 1000):
                async with self._semaphore:
                    result = await registration.handler.execute(
                        MappingProxyType(normalized_payload),
                        trace_id,
                    )
        except TimeoutError as exc:
            raise ToolExecutionTimeoutError(
                "Tool timed out: "
                f"{definition.tool_name}@{definition.version}"
            ) from exc
        except asyncio.CancelledError:
            # 租约丢失或应用关闭时必须让取消信号穿透，不能伪装成工具失败。
            raise
        except Exception as exc:
            # 不把第三方异常文本带到稳定错误消息中，避免泄漏凭据或查询内容。
            raise ToolExecutionError(
                "Tool execution failed: "
                f"{definition.tool_name}@{definition.version}"
            ) from exc

        return self._normalize_output(definition, result)

    def _normalize_input(
        self,
        payload: object,
    ) -> dict[str, Any]:
        """校验并复制输入，避免处理器修改工作流持有的原始字典。"""
        if not isinstance(payload, dict):
            raise AppValidationError("tool payload must be an object")
        encoded = self._encode_json(
            payload,
            "tool payload",
            AppValidationError,
        )
        if len(encoded) > self._config.max_input_bytes:
            raise AppValidationError(
                "tool payload exceeds "
                f"{self._config.max_input_bytes} bytes"
            )
        return json.loads(encoded)

    def _normalize_output(
        self,
        definition: ToolDefinition,
        result: object,
    ) -> dict[str, Any]:
        """校验、限制并复制处理器输出，阻断不可序列化或超大结果。"""
        tool_key = f"{definition.tool_name}@{definition.version}"
        if not isinstance(result, dict):
            raise ToolExecutionError(
                f"Tool result must be an object: {tool_key}"
            )
        encoded = self._encode_json(
            result,
            "tool result",
            ToolExecutionError,
        )
        if len(encoded) > self._config.max_output_bytes:
            raise ToolExecutionError(
                "Tool result exceeds "
                f"{self._config.max_output_bytes} bytes: {tool_key}"
            )
        return json.loads(encoded)

    @staticmethod
    def _encode_json(
        value: object,
        field_name: str,
        error_type: type[AppValidationError]
        | type[ToolExecutionError],
    ) -> bytes:
        """采用严格JSON编码，拒绝NaN、对象句柄等不稳定值。"""
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError) as exc:
            raise error_type(
                f"{field_name} must be JSON serializable"
            ) from exc

    @staticmethod
    def _validate_trace_id(trace_id: str) -> None:
        """限制链路标识长度与首尾空白，避免污染日志字段。"""
        if (
            not isinstance(trace_id, str)
            or not 1 <= len(trace_id) <= 128
            or trace_id != trace_id.strip()
        ):
            raise AppValidationError("trace_id is invalid")
