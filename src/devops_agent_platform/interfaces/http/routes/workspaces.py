import re
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from devops_agent_platform.application.exceptions import PreconditionRequiredError
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.workspace_service import (
    WorkspaceService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_workspace_service,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.workspace import WorkspaceConfig
from devops_agent_platform.interfaces.http.dto import UpsertWorkspaceRequest
from devops_agent_platform.interfaces.http.header_params import IdempotencyKeyHeader
from devops_agent_platform.interfaces.http.path_params import TenantPath, WorkspacePath
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["workspaces"])
_ETAG_PATTERN = re.compile(r'^"([0-9]+)"$')
_READ_SCOPE = "workspaces:read"
_WRITE_SCOPE = "workspaces:write"

AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
WorkspaceServiceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]
IfMatchHeader = Annotated[str | None, Header(alias="If-Match", max_length=32)]


def get_expected_revision(if_match: IfMatchHeader = None) -> int:
    if if_match is None:
        raise PreconditionRequiredError("If-Match header is required")
    match = _ETAG_PATTERN.fullmatch(if_match)
    if match is None:
        raise AppValidationError('If-Match must be a quoted integer, for example "0"')
    return int(match.group(1))


ExpectedRevisionDep = Annotated[int, Depends(get_expected_revision)]


@router.put(
    "/admin/tenants/{tenant_id}/workspaces/{workspace_id}",
    response_model=ResponseEnvelope,
)
async def upsert_workspace(
    tenant_id: TenantPath,
    workspace_id: WorkspacePath,
    payload: UpsertWorkspaceRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: WorkspaceServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_revision: ExpectedRevisionDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, _WRITE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.upsert(
        payload.to_command(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.workspace.revision}"'
    return success_response(result.to_dict(), trace_id)


@router.get(
    "/admin/tenants/{tenant_id}/workspaces/{workspace_id}",
    response_model=ResponseEnvelope,
)
async def get_workspace(
    tenant_id: TenantPath,
    workspace_id: WorkspacePath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: WorkspaceServiceDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    workspace = await service.get(tenant_id, workspace_id)
    response.headers["ETag"] = f'"{workspace.revision}"'
    return success_response(_serialize(workspace), get_trace_id(request))


@router.get(
    "/admin/tenants/{tenant_id}/workspaces",
    response_model=ResponseEnvelope,
)
async def list_workspaces(
    tenant_id: TenantPath,
    request: Request,
    principal: AdminPrincipalDep,
    service: WorkspaceServiceDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    workspaces = await service.list(tenant_id)
    return success_response(
        {"items": [_serialize(item) for item in workspaces]},
        get_trace_id(request),
    )


def _serialize(workspace: WorkspaceConfig) -> dict:
    payload = asdict(workspace)
    payload["updated_at"] = workspace.updated_at.isoformat()
    return payload
