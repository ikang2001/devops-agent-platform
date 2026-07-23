from devops_agent_platform.application.services.alert_service import (
    AlertApplicationService,
)
from devops_agent_platform.application.services.rca_service import RCAApplicationService
from devops_agent_platform.infrastructure.adapters.stub.audit import StubAuditLog
from devops_agent_platform.infrastructure.adapters.stub.events import StubEventPublisher
from devops_agent_platform.infrastructure.adapters.stub.repositories import (
    StubAlertRepository,
    StubIncidentRepository,
)
from devops_agent_platform.infrastructure.adapters.stub.workflow import (
    StubAgentWorkflow,
)


def get_alert_application_service() -> AlertApplicationService:
    """为 HTTP 处理器构建告警应用服务。

    Step 3 只装配 stub 适配器，用于显式呈现依赖图。
    应用服务仍会通过 NotImplementedInSkeleton 拒绝业务成功路径。
    """
    return AlertApplicationService(
        alert_repository=StubAlertRepository(),
        incident_repository=StubIncidentRepository(),
        event_publisher=StubEventPublisher(),
        audit_log=StubAuditLog(),
    )


def get_rca_application_service() -> RCAApplicationService:
    """为 HTTP 处理器构建 RCA 应用服务。

    workflow 适配器只是骨架占位，不能被视为生产执行器。
    Step 3 仅用它验证依赖注入形态是否清晰。
    """
    return RCAApplicationService(
        incident_repository=StubIncidentRepository(),
        agent_workflow=StubAgentWorkflow(),
        audit_log=StubAuditLog(),
    )
