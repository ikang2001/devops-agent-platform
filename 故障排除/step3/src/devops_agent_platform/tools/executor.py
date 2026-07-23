from typing import Any

from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton
from devops_agent_platform.tools.definition import ToolDefinition


class ToolExecutor:
    """带审计能力的工具执行入口骨架。"""

    async def execute(
        self,
        definition: ToolDefinition,
        payload: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        """在权限与风险校验通过后执行工具。"""
        raise NotImplementedInSkeleton()
