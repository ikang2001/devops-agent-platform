from collections.abc import Iterable
from threading import RLock

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.tools.definition import ToolDefinition

ToolKey = tuple[str, str]


def build_tool_key(tool_name: str, version: str) -> ToolKey:
    """构建精确工具键，并拒绝容易造成误匹配的空值和首尾空白。"""
    if not isinstance(tool_name, str) or not tool_name:
        raise AppValidationError("tool_name must be a non-empty string")
    if tool_name != tool_name.strip():
        raise AppValidationError(
            "tool_name must not contain surrounding whitespace"
        )
    if not isinstance(version, str) or not version:
        raise AppValidationError("version must be a non-empty string")
    if version != version.strip():
        raise AppValidationError(
            "version must not contain surrounding whitespace"
        )
    return tool_name, version


class ToolRegistry:
    """单进程内的工具元数据注册与查询入口。

    当前注册表适用于工具在应用启动阶段完成装配、运行期间以读取为主的场景。
    锁覆盖“重复检查 + 写入”完整临界区，避免多线程并发注册时覆盖已有定义。
    多实例之间的动态同步不属于本实现职责，后续应由配置发布或外部注册中心解决。
    """

    def __init__(self, definitions: Iterable[ToolDefinition] | None = None) -> None:
        """创建注册表，并按普通注册规则装载可选的初始工具集合。

        参数：
            definitions: 启动阶段需要预注册的工具定义；重复键会立即抛出冲突异常。
        """
        self._definitions: dict[ToolKey, ToolDefinition] = {}
        self._lock = RLock()

        for definition in definitions or ():
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        """注册一个不可变工具定义。

        同名工具允许多个版本并存，但同一个 ``(tool_name, version)`` 只能注册
        一次。禁止静默覆盖可以防止启动顺序变化导致线上实例加载不同实现。

        异常：
            AppValidationError: 传入对象不是 ToolDefinition。
            ConflictError: 同名同版本工具已经存在。
        """
        if not isinstance(definition, ToolDefinition):
            raise AppValidationError("definition must be a ToolDefinition")

        key = build_tool_key(definition.tool_name, definition.version)
        with self._lock:
            if key in self._definitions:
                raise ConflictError(
                    "Tool already registered: "
                    f"{definition.tool_name}@{definition.version}"
                )
            self._definitions[key] = definition

    def get(self, tool_name: str, version: str) -> ToolDefinition:
        """按工具名和版本加载定义，不对缺失版本进行隐式降级。

        明确版本可保证工作流重放时使用相同契约；自动选择“最新版本”容易让历史
        任务在重试后产生不同结果。

        异常：
            AppValidationError: 查询键为空或包含首尾空白。
            ResourceNotFound: 指定名称和版本不存在。
        """
        key = build_tool_key(tool_name, version)
        with self._lock:
            definition = self._definitions.get(key)

        if definition is None:
            raise ResourceNotFound(f"Tool not found: {tool_name}@{version}")
        return definition

    def list_tools(self) -> list[ToolDefinition]:
        """返回按名称和版本排序的工具定义快照。

        返回新列表可以避免调用方修改注册表内部容器；确定性排序则便于配置审计、
        管理接口分页和测试结果比对。
        """
        with self._lock:
            return [
                self._definitions[key]
                for key in sorted(self._definitions)
            ]
