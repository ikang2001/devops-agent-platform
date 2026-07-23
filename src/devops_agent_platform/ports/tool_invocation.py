from typing import Protocol

from devops_agent_platform.domain.models.tool_invocation import ToolInvocation


class ToolInvocationRepositoryPort(Protocol):
    """工具调用审计记录的持久化端口。"""

    async def save(self, invocation: ToolInvocation) -> None:
        """在当前事务中写入一条完成态工具调用记录。"""
        ...

    async def list_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        limit: int = 100,
    ) -> list[ToolInvocation]:
        """按租户和工作流查询有限数量的调用记录。"""
        ...
