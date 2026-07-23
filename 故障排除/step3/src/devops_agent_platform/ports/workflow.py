from typing import Protocol

from devops_agent_platform.application.commands.rca import StartRCACommand


class AgentWorkflowPort(Protocol):
    """Agent 工作流启动端口。"""

    async def start(self, command: StartRCACommand) -> str:
        """启动 RCA 工作流并返回 workflow_run_id。"""
        ...
