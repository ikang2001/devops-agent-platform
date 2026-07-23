from devops_agent_platform.application.commands.rca import StartRCACommand
from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton


class StubAgentWorkflow:
    """Agent 工作流占位实现，不能返回伪造的 workflow id。"""

    async def start(self, command: StartRCACommand) -> str:
        raise NotImplementedInSkeleton("StubAgentWorkflow is a Step 3 placeholder")
