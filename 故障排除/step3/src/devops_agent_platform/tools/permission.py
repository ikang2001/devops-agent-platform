from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton
from devops_agent_platform.tools.definition import ToolDefinition


class ToolPermissionChecker:
    """租户、用户、服务和风险等级维度的工具授权校验骨架。"""

    async def check(
        self,
        definition: ToolDefinition,
        tenant_id: str,
        operator_id: str | None,
    ) -> None:
        """校验调用方是否允许调用指定工具。"""
        raise NotImplementedInSkeleton()
