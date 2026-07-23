import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from devops_agent_platform.application.exceptions import (
    PreconditionRequiredError,
)
from devops_agent_platform.application.queries.rca_results import (
    GetRCAExecutionResultQuery,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.rca_cancellation_service import (
    RCACancellationService,
)
from devops_agent_platform.application.services.rca_query_service import (
    RCAExecutionQueryService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_rca_cancellation_service,
    get_rca_query_service,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.interfaces.http.dto import (
    CancelRCAWorkflowRequest,
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

router = APIRouter(tags=["rca-results"])
_READ_SCOPE = "rca:read"
_CANCEL_SCOPE = "rca:cancel"
_ETAG_PATTERN = re.compile(r'^"([1-9][0-9]*)"$')

AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
RCAQueryServiceDep = Annotated[
    RCAExecutionQueryService,
    Depends(get_rca_query_service),
]
RCACancellationServiceDep = Annotated[
    RCACancellationService,
    Depends(get_rca_cancellation_service),
]
ResultLimit = Annotated[int, Query(ge=1, le=100)]
IfMatchHeader = Annotated[
    str | None,
    Header(alias="If-Match", max_length=32),
]


def get_expected_workflow_version(
    if_match: IfMatchHeader = None,
) -> int:
    """把强 ETag 解析为 WorkflowRun 期望版本。"""
    if if_match is None:
        raise PreconditionRequiredError("If-Match header is required")
    match = _ETAG_PATTERN.fullmatch(if_match)
    if match is None:
        raise AppValidationError(
            'If-Match must be a quoted positive integer, for example "1"'
        )
    return int(match.group(1))


ExpectedWorkflowVersionDep = Annotated[
    int,
    Depends(get_expected_workflow_version),
]


@router.get(
    "/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}/result",
    response_model=ResponseEnvelope,
)
async def get_rca_execution_result(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: RCAQueryServiceDep,
    limit: ResultLimit = 100,
) -> dict:
    """返回经过租户授权和容量限制的 RCA 执行结果。"""
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    result = await service.get_result(
        GetRCAExecutionResultQuery(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            limit=limit,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), get_trace_id(request))


@router.post(
    (
        "/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}"
        "/cancellation"
    ),
    response_model=ResponseEnvelope,
)
async def cancel_rca_workflow(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    payload: CancelRCAWorkflowRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: RCACancellationServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedWorkflowVersionDep,
) -> dict:
    """管理员取消明确版本的 RCA 工作流，并返回新 ETag。"""
    principal.require_tenant_scope(tenant_id, _CANCEL_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.cancel(
        payload.to_command(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), trace_id)
