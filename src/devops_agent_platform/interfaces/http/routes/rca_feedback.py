from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from devops_agent_platform.application.queries.rca_feedback import (
    GetRCAFeedbackEvaluationCandidateQuery,
    ListRCAFeedbackQuery,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.rca_feedback_service import (
    RCAFeedbackApplicationService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_rca_feedback_service,
)
from devops_agent_platform.interfaces.http.dto import (
    CreateRCAFeedbackRequest,
)
from devops_agent_platform.interfaces.http.header_params import (
    IdempotencyKeyHeader,
)
from devops_agent_platform.interfaces.http.path_params import (
    FeedbackPath,
    TenantPath,
    WorkflowRunPath,
)
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["rca-feedback"])
_READ_SCOPE = "rca_feedback:read"
_WRITE_SCOPE = "rca_feedback:write"
_EXPORT_SCOPE = "rca_feedback:export"

AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
RCAFeedbackServiceDep = Annotated[
    RCAFeedbackApplicationService,
    Depends(get_rca_feedback_service),
]
FeedbackListLimit = Annotated[int, Query(ge=1, le=100)]


@router.post(
    ("/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}/feedback"),
    response_model=ResponseEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def create_rca_feedback(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    payload: CreateRCAFeedbackRequest,
    request: Request,
    principal: AdminPrincipalDep,
    service: RCAFeedbackServiceDep,
    idempotency_key: IdempotencyKeyHeader,
) -> dict:
    """为可信 RCA 报告追加一条不可变人工复核记录。"""
    principal.require_tenant_scope(tenant_id, _WRITE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.create(
        payload.to_command(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    return success_response(result.to_dict(), trace_id)


@router.get(
    ("/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}/feedback"),
    response_model=ResponseEnvelope,
)
async def list_rca_feedback(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    request: Request,
    principal: AdminPrincipalDep,
    service: RCAFeedbackServiceDep,
    limit: FeedbackListLimit = 50,
) -> dict:
    """按时间倒序读取租户内有限反馈历史。"""
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    result = await service.list(
        ListRCAFeedbackQuery(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            limit=limit,
        )
    )
    return success_response(
        {"items": [item.to_dict() for item in result]},
        get_trace_id(request),
    )


@router.get(
    (
        "/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}"
        "/feedback/{feedback_id}/evaluation-candidate"
    ),
    response_model=ResponseEnvelope,
)
async def get_rca_feedback_evaluation_candidate(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    feedback_id: FeedbackPath,
    request: Request,
    principal: AdminPrincipalDep,
    service: RCAFeedbackServiceDep,
) -> dict:
    """导出仍需人工策展的评测候选，不触发模型调用或自动发布。"""
    principal.require_tenant_scope(tenant_id, _EXPORT_SCOPE)
    result = await service.get_evaluation_candidate(
        GetRCAFeedbackEvaluationCandidateQuery(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            feedback_id=feedback_id,
        )
    )
    return success_response(result.to_dict(), get_trace_id(request))
