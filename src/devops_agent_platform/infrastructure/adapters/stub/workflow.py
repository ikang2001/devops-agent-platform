from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton


class StubAgentWorkflow:
    """Agent 工作流占位实现，不能返回伪造的 workflow id。"""

    async def execute(self, command: ExecuteRCAWorkflowCommand) -> None:
        raise NotImplementedInSkeleton(
            "StubAgentWorkflow is unavailable in skeleton mode"
        )
