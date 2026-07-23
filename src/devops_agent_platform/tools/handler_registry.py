from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.registry import (
    ToolKey,
    build_tool_key,
)


class ToolHandler(Protocol):
    """一个明确版本工具的异步处理器契约。"""

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        """执行只读或受控操作，并返回JSON对象。"""
        ...


@dataclass(frozen=True)
class ToolHandlerRegistration:
    """工具元数据与可执行处理器之间的不可变绑定。"""

    definition: ToolDefinition
    handler: ToolHandler

    def __post_init__(self) -> None:
        """在启动装配阶段拒绝无效定义和不可调用处理器。"""
        if not isinstance(self.definition, ToolDefinition):
            raise AppValidationError(
                "definition must be a ToolDefinition"
            )
        execute = getattr(self.handler, "execute", None)
        if not callable(execute):
            raise AppValidationError(
                "handler must provide a callable execute method"
            )


class ToolHandlerRegistry:
    """单进程内工具处理器的精确版本注册表。

    注册动作应在应用启动阶段完成。运行期间只按名称和版本读取，不支持静默
    覆盖，也不自动选择最新版本，避免不同实例因装配顺序产生执行差异。
    """

    def __init__(
        self,
        registrations: Iterable[ToolHandlerRegistration] | None = None,
    ) -> None:
        """创建注册表，并按普通冲突规则装载初始绑定。"""
        self._registrations: dict[
            ToolKey,
            ToolHandlerRegistration,
        ] = {}
        self._lock = RLock()

        for registration in registrations or ():
            if not isinstance(registration, ToolHandlerRegistration):
                raise AppValidationError(
                    "registrations must contain ToolHandlerRegistration"
                )
            self._register_registration(registration)

    def register(
        self,
        definition: ToolDefinition,
        handler: ToolHandler,
    ) -> None:
        """绑定一个明确版本处理器，同名同版本不允许覆盖。"""
        registration = ToolHandlerRegistration(definition, handler)
        self._register_registration(registration)

    def _register_registration(
        self,
        registration: ToolHandlerRegistration,
    ) -> None:
        """原子保存已校验绑定，供构造装载和普通注册复用。"""
        definition = registration.definition
        key = build_tool_key(
            definition.tool_name,
            definition.version,
        )
        with self._lock:
            if key in self._registrations:
                raise ConflictError(
                    "Tool handler already registered: "
                    f"{definition.tool_name}@{definition.version}"
                )
            self._registrations[key] = registration

    def get(
        self,
        tool_name: str,
        version: str,
    ) -> ToolHandlerRegistration:
        """按精确名称和版本查询处理器绑定。"""
        key = build_tool_key(tool_name, version)
        with self._lock:
            registration = self._registrations.get(key)

        if registration is None:
            raise ResourceNotFound(
                f"Tool handler not found: {tool_name}@{version}"
            )
        return registration

    def list_handlers(self) -> list[ToolHandlerRegistration]:
        """返回确定性排序的处理器绑定快照。"""
        with self._lock:
            return [
                self._registrations[key]
                for key in sorted(self._registrations)
            ]
