from typing import Annotated

from fastapi import APIRouter, Depends, Request

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.notification_service import (
    NotificationApplicationService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_notification_service,
)
from devops_agent_platform.interfaces.http.dto import (
    SendWorkflowNotificationRequest,
)
from devops_agent_platform.interfaces.http.header_params import (
    IdempotencyKeyHeader,
)
from devops_agent_platform.interfaces.http.path_params import (
    TenantPath,
    WorkflowRunPath,
)
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["notifications"])
_SEND_SCOPE = "notifications:send"

AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
NotificationServiceDep = Annotated[
    NotificationApplicationService,
    Depends(get_notification_service),
]


@router.post(
    ("/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}/notifications"),
    response_model=ResponseEnvelope,
)
async def send_workflow_notification(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    payload: SendWorkflowNotificationRequest,
    request: Request,
    principal: AdminPrincipalDep,
    service: NotificationServiceDep,
    idempotency_key: IdempotencyKeyHeader,
) -> dict:
    """把成功 RCA 的服务端派生摘要投递到显式供应商。"""
    principal.require_tenant_scope(tenant_id, _SEND_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.send(
        payload.to_command(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    return success_response(result.to_dict(), trace_id)
