from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton
from devops_agent_platform.ports.workflow import AgentWorkflowResult


class AgentWorkflowExecutor:
    """受控 RCA 工作流执行器骨架。

    生产实现必须约束固定工作流、工具权限、执行 Trace 记录，并在高风险动作前
    强制人工确认。
    """

    async def execute(
        self,
        command: ExecuteRCAWorkflowCommand,
    ) -> AgentWorkflowResult:
        """启动 RCA 工作流。

        异常：
            NotImplementedInSkeleton: Skeleton 模式不执行真实工作流。
        """
        raise NotImplementedInSkeleton()
