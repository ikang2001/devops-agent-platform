import re
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    Request,
    Response,
)

from devops_agent_platform.application.commands.ticket_drafts import (
    CreateTicketDraftCommand,
)
from devops_agent_platform.application.exceptions import (
    PreconditionRequiredError,
)
from devops_agent_platform.application.queries.ticket_drafts import (
    GetTicketDraftQuery,
    ListTicketSubmissionsQuery,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.ticket_draft_service import (
    TicketDraftApplicationService,
)
from devops_agent_platform.application.services.ticket_submission_service import (
    TicketSubmissionApplicationService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_ticket_draft_service,
    get_ticket_submission_service,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.interfaces.http.dto import (
    TicketDraftDecisionRequest,
    TicketSubmissionRequest,
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

router = APIRouter(tags=["ticket-drafts"])
_READ_SCOPE = "ticket_drafts:read"
_WRITE_SCOPE = "ticket_drafts:write"
_APPROVE_SCOPE = "ticket_drafts:approve"
_SUBMIT_SCOPE = "ticket_drafts:submit"
_ETAG_PATTERN = re.compile(r'^"([0-9]+)"$')

AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
TicketDraftServiceDep = Annotated[
    TicketDraftApplicationService,
    Depends(get_ticket_draft_service),
]
TicketSubmissionServiceDep = Annotated[
    TicketSubmissionApplicationService,
    Depends(get_ticket_submission_service),
]
SubmissionListLimit = Annotated[int, Query(ge=1, le=100)]
IfMatchHeader = Annotated[
    str | None,
    Header(alias="If-Match", max_length=32),
]


def get_expected_version(
    if_match: IfMatchHeader = None,
) -> int:
    """把强 ETag 解析为 Ticket Draft 期望版本。"""
    if if_match is None:
        raise PreconditionRequiredError(
            "If-Match header is required"
        )
    match = _ETAG_PATTERN.fullmatch(if_match)
    if match is None:
        raise AppValidationError(
            'If-Match must be a quoted positive integer, for example "1"'
        )
    return int(match.group(1))


ExpectedVersionDep = Annotated[int, Depends(get_expected_version)]


@router.post(
    (
        "/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}"
        "/ticket-draft"
    ),
    response_model=ResponseEnvelope,
)
async def create_ticket_draft(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: TicketDraftServiceDep,
    idempotency_key: IdempotencyKeyHeader,
) -> dict:
    """从已成功工作流的可信报告生成本地草稿。"""
    principal.require_tenant_scope(tenant_id, _WRITE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.create(
        CreateTicketDraftCommand(
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
    (
        "/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}"
        "/ticket-draft"
    ),
    response_model=ResponseEnvelope,
)
async def get_ticket_draft(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: TicketDraftServiceDep,
) -> dict:
    """读取工作流对应的本地草稿。"""
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    result = await service.get(
        GetTicketDraftQuery(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), get_trace_id(request))


@router.post(
    (
        "/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}"
        "/ticket-draft/decision"
    ),
    response_model=ResponseEnvelope,
)
async def decide_ticket_draft(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    payload: TicketDraftDecisionRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: TicketDraftServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedVersionDep,
) -> dict:
    """对明确版本的本地工单草稿作人工确认。"""
    principal.require_tenant_scope(tenant_id, _APPROVE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.decide(
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


@router.get(
    (
        "/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}"
        "/ticket-draft/submissions"
    ),
    response_model=ResponseEnvelope,
)
async def list_ticket_draft_submissions(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    request: Request,
    principal: AdminPrincipalDep,
    service: TicketSubmissionServiceDep,
    limit: SubmissionListLimit = 50,
) -> dict:
    """读取工作流下外部工单提交状态，不要求幂等键。"""
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    result = await service.list_by_workflow(
        ListTicketSubmissionsQuery(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            limit=limit,
        )
    )
    return success_response(
        {"items": [item.to_dict() for item in result]},
        get_trace_id(request),
    )


@router.post(
    (
        "/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}"
        "/ticket-draft/submissions"
    ),
    response_model=ResponseEnvelope,
)
async def submit_ticket_draft(
    tenant_id: TenantPath,
    workflow_run_id: WorkflowRunPath,
    payload: TicketSubmissionRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: TicketSubmissionServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedVersionDep,
) -> dict:
    """登记已审批草稿的外部工单提交请求。"""
    principal.require_tenant_scope(tenant_id, _SUBMIT_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.request_submission(
        payload.to_command(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            expected_draft_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), trace_id)
