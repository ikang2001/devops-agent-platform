from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton
from devops_agent_platform.tools.definition import ToolDefinition


class ToolRegistry:
    """工具元数据注册与查询的统一入口。"""

    def register(self, definition: ToolDefinition) -> None:
        """注册一个工具定义。

        Step 3 只定义 API 形态。真实注册、重复校验和版本治理会在接入第一个
        生产工具时实现。
        """
        raise NotImplementedInSkeleton()

    def get(self, tool_name: str, version: str) -> ToolDefinition:
        """按工具名和版本加载一个工具定义。"""
        raise NotImplementedInSkeleton()

    def list_tools(self) -> list[ToolDefinition]:
        """列出已注册的工具定义。"""
        raise NotImplementedInSkeleton()
