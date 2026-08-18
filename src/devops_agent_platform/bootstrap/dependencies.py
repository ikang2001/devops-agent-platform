import asyncio
from typing import Annotated

from fastapi import Header, Request

from devops_agent_platform.application.exceptions import (
    AuthenticationServiceError,
    RuntimeUnavailableError,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.alert_service import (
    AlertApplicationService,
)
from devops_agent_platform.application.services.change_event_service import (
    ChangeEventApplicationService,
)
from devops_agent_platform.application.services.dataset_release_service import (
    DatasetReleaseService,
)
from devops_agent_platform.application.services.incident_query_service import (
    IncidentQueryService,
)
from devops_agent_platform.application.services.incident_resolution_service import (
    IncidentResolutionService,
)
from devops_agent_platform.application.services.notification_service import (
    NotificationApplicationService,
)
from devops_agent_platform.application.services.rca_cancellation_service import (
    RCACancellationService,
)
from devops_agent_platform.application.services.rca_feedback_service import (
    RCAFeedbackApplicationService,
)
from devops_agent_platform.application.services.rca_query_service import (
    RCAExecutionQueryService,
)
from devops_agent_platform.application.services.rca_service import RCAApplicationService
from devops_agent_platform.application.services.remediation_service import (
    RemediationApplicationService,
)
from devops_agent_platform.application.services.runbook_admin_service import (
    RunbookAdminService,
)
from devops_agent_platform.application.services.ticket_draft_service import (
    TicketDraftApplicationService,
)
from devops_agent_platform.application.services.ticket_submission_service import (
    TicketSubmissionApplicationService,
)
from devops_agent_platform.application.services.tool_permission_admin_service import (
    ToolPermissionAdminService,
)
from devops_agent_platform.application.services.workspace_service import (
    WorkspaceService,
)
from devops_agent_platform.bootstrap.runtime import ApplicationRuntime
from devops_agent_platform.domain.exceptions import AuthenticationRequired
from devops_agent_platform.domain.policies.incident_creation import (
    IncidentCreationPolicy,
)
from devops_agent_platform.infrastructure.adapters.stub.unit_of_work import (
    StubUnitOfWork,
)
from devops_agent_platform.infrastructure.identifiers import UUIDIdentifierGenerator
from devops_agent_platform.ports.authentication import (
    AdministratorAuthenticatorPort,
    AlertWebhookAuthenticatorPort,
)

AuthorizationHeader = Annotated[
    str | None,
    Header(alias="Authorization", max_length=8192),
]
WebhookTimestampHeader = Annotated[
    str | None,
    Header(alias="X-DevOps-Agent-Timestamp", max_length=32),
]
WebhookSignatureHeader = Annotated[
    str | None,
    Header(alias="X-DevOps-Agent-Signature", max_length=128),
]


async def verify_alert_webhook(
    request: Request,
    timestamp: WebhookTimestampHeader = None,
    signature: WebhookSignatureHeader = None,
) -> None:
    """启用机器认证时，以原始请求体完成Webhook验签。"""
    authenticator: AlertWebhookAuthenticatorPort | None = getattr(
        request.app.state,
        "alert_webhook_authenticator",
        None,
    )
    if authenticator is None:
        return
    body = await request.body()
    authenticator.verify(
        timestamp=timestamp,
        signature=signature,
        body=body,
    )


def get_alert_application_service(request: Request) -> AlertApplicationService:
    """从应用生命周期容器获取已装配的告警应用服务。

    请求处理阶段不能临时创建数据库引擎或Kafka Producer。若lifespan尚未完成，
    返回稳定503而不是触发属性错误。
    """
    service = getattr(request.app.state, "alert_application_service", None)
    if service is None:
        raise RuntimeUnavailableError()
    return service


def get_change_event_application_service(
    request: Request,
) -> ChangeEventApplicationService:
    """从应用生命周期容器获取变更事件接入服务。"""
    service = getattr(request.app.state, "change_event_application_service", None)
    if service is None:
        raise RuntimeUnavailableError("Change event service is unavailable")
    return service


def get_application_runtime(request: Request) -> ApplicationRuntime:
    """获取已启动的应用运行时，供就绪检查等进程级接口使用。"""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise RuntimeUnavailableError()
    return runtime


def build_skeleton_alert_service() -> AlertApplicationService:
    """构建显式返回501的无外部依赖测试服务。"""
    return AlertApplicationService(
        unit_of_work_factory=StubUnitOfWork,
        incident_policy=IncidentCreationPolicy(),
        identifier_generator=UUIDIdentifierGenerator(),
    )


def build_skeleton_change_event_service() -> ChangeEventApplicationService:
    """构建显式返回 501 的无外部依赖变更接入服务。"""
    return ChangeEventApplicationService(
        unit_of_work_factory=StubUnitOfWork,
        identifier_generator=UUIDIdentifierGenerator(),
    )


def get_rca_application_service(request: Request) -> RCAApplicationService:
    """从应用生命周期容器获取RCA应用服务。"""
    service = getattr(request.app.state, "rca_application_service", None)
    if service is None:
        raise RuntimeUnavailableError()
    return service


def build_skeleton_rca_service() -> RCAApplicationService:
    """构建通过Stub UoW明确返回501的RCA骨架服务。"""
    return RCAApplicationService(
        unit_of_work_factory=StubUnitOfWork,
        identifier_generator=UUIDIdentifierGenerator(),
    )


def get_rca_query_service(request: Request) -> RCAExecutionQueryService:
    """从应用生命周期容器获取 RCA 执行结果查询服务。"""
    service = getattr(request.app.state, "rca_query_service", None)
    if service is None:
        raise RuntimeUnavailableError("RCA result query is unavailable")
    return service


def get_rca_feedback_service(
    request: Request,
) -> RCAFeedbackApplicationService:
    """从应用生命周期容器获取 RCA 人工反馈服务。"""
    service = getattr(request.app.state, "rca_feedback_service", None)
    if service is None:
        raise RuntimeUnavailableError("RCA feedback service is not initialized")
    return service


def get_remediation_service(
    request: Request,
) -> RemediationApplicationService:
    """从应用生命周期容器获取受控自动修复服务。"""
    service = getattr(request.app.state, "remediation_service", None)
    if service is None:
        raise RuntimeUnavailableError("Remediation controller is not configured")
    return service


def get_notification_service(
    request: Request,
) -> NotificationApplicationService:
    """返回已装配的供应商通知服务。"""
    service = getattr(request.app.state, "notification_service", None)
    if service is None:
        raise RuntimeUnavailableError("Notification service is not configured")
    return service


def get_rca_cancellation_service(request: Request) -> RCACancellationService:
    """从应用生命周期容器获取 RCA 工作流取消服务。"""
    service = getattr(request.app.state, "rca_cancellation_service", None)
    if service is None:
        raise RuntimeUnavailableError("RCA cancellation is unavailable")
    return service


def get_incident_resolution_service(
    request: Request,
) -> IncidentResolutionService:
    """从应用生命周期容器获取事故人工解决服务。"""
    service = getattr(
        request.app.state,
        "incident_resolution_service",
        None,
    )
    if service is None:
        raise RuntimeUnavailableError("Incident resolution service is unavailable")
    return service


def get_incident_query_service(request: Request) -> IncidentQueryService:
    """从应用生命周期容器获取事故管理端查询服务。"""
    service = getattr(request.app.state, "incident_query_service", None)
    if service is None:
        raise RuntimeUnavailableError("Incident query service is unavailable")
    return service


def get_tool_permission_admin_service(
    request: Request,
) -> ToolPermissionAdminService:
    """从应用生命周期容器获取权限管理写服务。"""
    service = getattr(
        request.app.state,
        "tool_permission_admin_service",
        None,
    )
    if service is None:
        raise RuntimeUnavailableError("Tool permission administration is unavailable")
    return service


def get_runbook_admin_service(
    request: Request,
) -> RunbookAdminService:
    """从应用生命周期容器获取 Runbook 管理写服务。"""
    service = getattr(request.app.state, "runbook_admin_service", None)
    if service is None:
        raise RuntimeUnavailableError("Runbook administration is unavailable")
    return service


def get_workspace_service(request: Request) -> WorkspaceService:
    """从应用生命周期容器获取租户隔离的 Workspace 管理服务。"""
    service = getattr(request.app.state, "workspace_service", None)
    if service is None:
        raise RuntimeUnavailableError("Workspace administration is unavailable")
    return service


def get_dataset_release_service(request: Request) -> DatasetReleaseService:
    """从应用生命周期容器获取在线数据集审核与发布服务。"""
    service = getattr(request.app.state, "dataset_release_service", None)
    if service is None:
        raise RuntimeUnavailableError("Dataset release workflow is unavailable")
    return service


def get_ticket_draft_service(
    request: Request,
) -> TicketDraftApplicationService:
    """从应用生命周期容器获取本地工单草稿服务。"""
    service = getattr(request.app.state, "ticket_draft_service", None)
    if service is None:
        raise RuntimeUnavailableError("Ticket draft service is unavailable")
    return service


def get_ticket_submission_service(
    request: Request,
) -> TicketSubmissionApplicationService:
    """从应用生命周期容器获取外部工单提交请求服务。"""
    service = getattr(request.app.state, "ticket_submission_service", None)
    if service is None:
        raise RuntimeUnavailableError("Ticket submission service is unavailable")
    return service


async def get_administrator_principal(
    request: Request,
    authorization: AuthorizationHeader = None,
) -> AdministratorPrincipal:
    """解析Bearer头并调用外部认证端口生成可信管理员主体。"""
    bearer_token = _parse_bearer_token(authorization)
    authenticator: AdministratorAuthenticatorPort | None = getattr(
        request.app.state,
        "admin_authenticator",
        None,
    )
    if authenticator is None:
        raise RuntimeUnavailableError("Administrator authentication is not configured")
    try:
        principal = await authenticator.authenticate(bearer_token)
    except asyncio.CancelledError:
        raise
    except AuthenticationRequired:
        raise
    except AuthenticationServiceError:
        raise
    except Exception as exc:
        raise AuthenticationServiceError() from exc
    if not isinstance(principal, AdministratorPrincipal):
        raise AuthenticationServiceError(
            "Authentication service returned an invalid principal"
        )
    return principal


def _parse_bearer_token(authorization: str | None) -> str:
    """严格解析Bearer方案，拒绝空Token和其他认证方案。"""
    if authorization is None:
        raise AuthenticationRequired()
    scheme, separator, token = authorization.partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not token
        or token != token.strip()
        or _contains_ascii_control(token)
        or any(character.isspace() for character in token)
    ):
        raise AuthenticationRequired("Invalid Bearer credentials")
    return token


def _contains_ascii_control(value: str) -> bool:
    """拒绝不可见ASCII控制字符，避免污染认证审计链路。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
