import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request, Response

from devops_agent_platform.application.exceptions import (
    PreconditionRequiredError,
)
from devops_agent_platform.application.queries.remediation import (
    GetRemediationPlanQuery,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.remediation_service import (
    RemediationApplicationService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_remediation_service,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.interfaces.http.dto import (
    CreateRemediationPlanRequest,
    DecideRemediationPlanRequest,
    RemediationActionRequest,
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

router = APIRouter(tags=["remediation"])
_ETAG_PATTERN = re.compile(r'^"([0-9]+)"$')
_READ_SCOPE = "remediation:read"
_WRITE_SCOPE = "remediation:write"
_APPROVE_SCOPE = "remediation:approve"
_EXECUTE_SCOPE = "remediation:execute"
_ROLLBACK_SCOPE = "remediation:rollback"

AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
RemediationServiceDep = Annotated[
    RemediationApplicationService,
    Depends(get_remediation_service),
]
IfMatchHeader = Annotated[
    str | None,
    Header(alias="If-Match", max_length=32),
]
RemediationPlanPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


def get_expected_version(if_match: IfMatchHeader = None) -> int:
    if if_match is None:
        raise PreconditionRequiredError("If-Match header is required")
    match = _ETAG_PATTERN.fullmatch(if_match)
    if match is None or int(match.group(1)) < 1:
        raise AppValidationError(
            'If-Match must be a quoted positive integer, for example "1"'
        )
    return int(match.group(1))


ExpectedVersionDep = Annotated[int, Depends(get_expected_version)]


@router.post(
    (
        "/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}"
        "/remediation-plans"
    ),
    response_model=ResponseEnvelope,
)
async def create_remediation_plan(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    payload: CreateRemediationPlanRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: RemediationServiceDep,
    idempotency_key: IdempotencyKeyHeader,
) -> dict:
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
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), trace_id)


@router.get(
    "/admin/tenants/{tenant_id}/remediation-plans/{remediation_plan_id}",
    response_model=ResponseEnvelope,
)
async def get_remediation_plan(
    tenant_id: TenantPath,
    remediation_plan_id: RemediationPlanPath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: RemediationServiceDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    result = await service.get(
        GetRemediationPlanQuery(
            tenant_id=tenant_id,
            remediation_plan_id=remediation_plan_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), get_trace_id(request))


@router.post(
    (
        "/admin/tenants/{tenant_id}/remediation-plans/"
        "{remediation_plan_id}/decision"
    ),
    response_model=ResponseEnvelope,
)
async def decide_remediation_plan(
    tenant_id: TenantPath,
    remediation_plan_id: RemediationPlanPath,
    payload: DecideRemediationPlanRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: RemediationServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedVersionDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, _APPROVE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.decide(
        payload.to_command(
            tenant_id=tenant_id,
            remediation_plan_id=remediation_plan_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), trace_id)


@router.post(
    (
        "/admin/tenants/{tenant_id}/remediation-plans/"
        "{remediation_plan_id}/execute"
    ),
    response_model=ResponseEnvelope,
)
async def execute_remediation_plan(
    tenant_id: TenantPath,
    remediation_plan_id: RemediationPlanPath,
    payload: RemediationActionRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: RemediationServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedVersionDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, _EXECUTE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.execute(
        payload.to_execute_command(
            tenant_id=tenant_id,
            remediation_plan_id=remediation_plan_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), trace_id)


@router.post(
    (
        "/admin/tenants/{tenant_id}/remediation-plans/"
        "{remediation_plan_id}/rollback"
    ),
    response_model=ResponseEnvelope,
)
async def rollback_remediation_plan(
    tenant_id: TenantPath,
    remediation_plan_id: RemediationPlanPath,
    payload: RemediationActionRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: RemediationServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedVersionDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, _ROLLBACK_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.rollback(
        payload.to_rollback_command(
            tenant_id=tenant_id,
            remediation_plan_id=remediation_plan_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), trace_id)
