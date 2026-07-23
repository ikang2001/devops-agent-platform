from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton
from devops_agent_platform.ports.audit import AuditLogPort
from devops_agent_platform.ports.events import EventPublisherPort
from devops_agent_platform.ports.repositories import (
    AlertRepositoryPort,
    IncidentRepositoryPort,
)


class AlertApplicationService:
    """告警接入用例服务。

    这里依赖的是端口接口，而不是具体基础设施实现。Step 3 会故意拒绝
    业务成功路径，避免骨架阶段产生“假业务已完成”的误导。
    """

    def __init__(
        self,
        alert_repository: AlertRepositoryPort,
        incident_repository: IncidentRepositoryPort,
        event_publisher: EventPublisherPort,
        audit_log: AuditLogPort,
    ) -> None:
        self._alert_repository = alert_repository
        self._incident_repository = incident_repository
        self._event_publisher = event_publisher
        self._audit_log = audit_log

    async def receive_alert(self, command: ReceiveAlertCommand) -> dict[str, str]:
        """接收一条告警并返回告警/事故标识。

        参数：
            command: 由 HTTP 请求转换得到的应用层 Command。

        返回：
            后续实现业务流程后，返回包含 alert_id、incident_id、status、
            trace_id 的字典。

        异常：
            NotImplementedInSkeleton: Step 3 固定抛出，业务成功路径不能伪造。
        """
        raise NotImplementedInSkeleton()
