from devops_agent_platform.application.commands.rca import StartRCACommand
from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton


class AgentWorkflowExecutor:
    """受控 RCA 工作流执行器骨架。

    生产实现必须约束固定工作流、工具权限、执行 Trace 记录，并在高风险动作前
    强制人工确认。
    """

    async def start(self, command: StartRCACommand) -> str:
        """启动 RCA 工作流。

        异常：
            NotImplementedInSkeleton: Step 3 只暴露结构，不执行真实工作流。
        """
        raise NotImplementedInSkeleton()
