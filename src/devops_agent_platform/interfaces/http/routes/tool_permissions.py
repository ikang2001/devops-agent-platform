import re
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Request,
    Response,
)

from devops_agent_platform.application.commands.tool_permissions import (
    RevokeToolPermissionsCommand,
)
from devops_agent_platform.application.exceptions import (
    PreconditionRequiredError,
)
from devops_agent_platform.application.queries.tool_permissions import (
    GetToolPermissionsQuery,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.tool_permission_admin_service import (
    ToolPermissionAdminService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_tool_permission_admin_service,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.interfaces.http.dto import (
    SetToolPermissionsRequest,
)
from devops_agent_platform.interfaces.http.header_params import (
    IdempotencyKeyHeader,
)
from devops_agent_platform.interfaces.http.path_params import (
    OperatorPath,
    TenantPath,
)
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["tool-permissions"])
_ETAG_PATTERN = re.compile(r'^"([0-9]+)"$')
_READ_SCOPE = "tool_permissions:read"
_WRITE_SCOPE = "tool_permissions:write"

AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
PermissionAdminServiceDep = Annotated[
    ToolPermissionAdminService,
    Depends(get_tool_permission_admin_service),
]
IfMatchHeader = Annotated[
    str | None,
    Header(alias="If-Match", max_length=32),
]


def get_expected_version(
    if_match: IfMatchHeader = None,
) -> int:
    """把强ETag条件头转换为非负权限状态版本。"""
    if if_match is None:
        raise PreconditionRequiredError(
            "If-Match header is required"
        )
    match = _ETAG_PATTERN.fullmatch(if_match)
    if match is None:
        raise AppValidationError(
            'If-Match must be a quoted non-negative integer, for example "3"'
        )
    return int(match.group(1))


ExpectedVersionDep = Annotated[int, Depends(get_expected_version)]


@router.get(
    "/admin/tenants/{tenant_id}/operators/{operator_id}/tool-permissions",
    response_model=ResponseEnvelope,
)
async def get_tool_permissions(
    tenant_id: TenantPath,
    operator_id: OperatorPath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: PermissionAdminServiceDep,
) -> dict:
    """读取操作者当前工具权限快照，并返回状态版本ETag。"""
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    result = await service.get_current(
        GetToolPermissionsQuery(
            tenant_id=tenant_id,
            operator_id=operator_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), get_trace_id(request))


@router.put(
    "/admin/tenants/{tenant_id}/operators/{operator_id}/tool-permissions",
    response_model=ResponseEnvelope,
)
async def set_tool_permissions(
    tenant_id: TenantPath,
    operator_id: OperatorPath,
    payload: SetToolPermissionsRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: PermissionAdminServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedVersionDep,
) -> dict:
    """授予或完整替换目标操作者权限。"""
    principal.require_tenant_scope(tenant_id, _WRITE_SCOPE)
    trace_id = get_trace_id(request)
    command = payload.to_command(
        tenant_id=tenant_id,
        operator_id=operator_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        requested_by=principal.admin_id,
        trace_id=trace_id,
    )
    result = await service.set_permissions(command)
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), trace_id)


@router.delete(
    "/admin/tenants/{tenant_id}/operators/{operator_id}/tool-permissions",
    response_model=ResponseEnvelope,
)
async def revoke_tool_permissions(
    tenant_id: TenantPath,
    operator_id: OperatorPath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: PermissionAdminServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedVersionDep,
) -> dict:
    """撤销目标操作者当前生效的全部工具权限。"""
    principal.require_tenant_scope(tenant_id, _WRITE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.revoke_permissions(
        RevokeToolPermissionsCommand(
            tenant_id=tenant_id,
            operator_id=operator_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(result.to_dict(), trace_id)
