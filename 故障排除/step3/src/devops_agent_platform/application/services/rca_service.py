from devops_agent_platform.application.commands.rca import StartRCACommand
from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton
from devops_agent_platform.ports.audit import AuditLogPort
from devops_agent_platform.ports.repositories import IncidentRepositoryPort
from devops_agent_platform.ports.workflow import AgentWorkflowPort


class RCAApplicationService:
    """RCA 工作流启动用例服务。"""

    def __init__(
        self,
        incident_repository: IncidentRepositoryPort,
        agent_workflow: AgentWorkflowPort,
        audit_log: AuditLogPort,
    ) -> None:
        self._incident_repository = incident_repository
        self._agent_workflow = agent_workflow
        self._audit_log = audit_log

    async def start_rca(self, command: StartRCACommand) -> dict[str, str]:
        """为事故启动 RCA 工作流。

        Step 3 只定义调用契约。工作流不能返回伪造的成功响应。
        """
        raise NotImplementedInSkeleton()
