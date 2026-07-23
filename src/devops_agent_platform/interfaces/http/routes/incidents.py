import re
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    Request,
    Response,
    status,
)

from devops_agent_platform.application.commands.rca import StartRCACommand
from devops_agent_platform.application.exceptions import (
    PreconditionRequiredError,
)
from devops_agent_platform.application.queries.incidents import (
    GetIncidentQuery,
    ListIncidentsQuery,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.incident_query_service import (
    IncidentQueryService,
)
from devops_agent_platform.application.services.incident_resolution_service import (
    IncidentResolutionService,
)
from devops_agent_platform.application.services.rca_service import RCAApplicationService
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_incident_query_service,
    get_incident_resolution_service,
    get_rca_application_service,
)
from devops_agent_platform.domain.enums import IncidentStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.interfaces.http.dto import (
    CloseIncidentRequest,
    ResolveIncidentRequest,
    StartRCARequest,
)
from devops_agent_platform.interfaces.http.header_params import (
    IdempotencyKeyHeader,
)
from devops_agent_platform.interfaces.http.path_params import (
    IncidentPath,
    TenantPath,
)
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["incidents"])
_READ_SCOPE = "incidents:read"
_RCA_SCOPE = "incidents:rca"
_RESOLVE_SCOPE = "incidents:resolve"
_CLOSE_SCOPE = "incidents:close"
_ETAG_PATTERN = re.compile(r'^"([1-9][0-9]*)"$')
RCAServiceDep = Annotated[
    RCAApplicationService,
    Depends(get_rca_application_service),
]
IncidentResolutionServiceDep = Annotated[
    IncidentResolutionService,
    Depends(get_incident_resolution_service),
]
IncidentQueryServiceDep = Annotated[
    IncidentQueryService,
    Depends(get_incident_query_service),
]
AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
IncidentStatusesQuery = Annotated[
    list[IncidentStatus] | None,
    Query(alias="status"),
]
IncidentListLimit = Annotated[int, Query(ge=1, le=100)]
IncidentCursorQuery = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=1024,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
]
IfMatchHeader = Annotated[
    str | None,
    Header(alias="If-Match", max_length=32),
]


def get_expected_incident_version(
    if_match: IfMatchHeader = None,
) -> int:
    """把强 ETag 解析为事故聚合期望版本。"""
    if if_match is None:
        raise PreconditionRequiredError("If-Match header is required")
    match = _ETAG_PATTERN.fullmatch(if_match)
    if match is None:
        raise AppValidationError(
            'If-Match must be a quoted positive integer, for example "1"'
        )
    return int(match.group(1))


ExpectedIncidentVersionDep = Annotated[
    int,
    Depends(get_expected_incident_version),
]


@router.post(
    "/admin/tenants/{tenant_id}/incidents/{incident_id}/rca",
    response_model=ResponseEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_rca(
    tenant_id: TenantPath,
    incident_id: IncidentPath,
    request: Request,
    principal: AdminPrincipalDep,
    service: RCAServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    payload: StartRCARequest | None = None,
) -> dict:
    """以认证管理员身份持久化 RCA 运行请求。"""
    del payload
    principal.require_tenant_scope(tenant_id, _RCA_SCOPE)
    trace_id = get_trace_id(request)
    command = StartRCACommand(
        incident_id=incident_id,
        tenant_id=tenant_id,
        operator_id=principal.admin_id,
        idempotency_key=idempotency_key,
        trace_id=trace_id,
    )
    result = await service.start_rca(command)
    return success_response(data=result.to_dict(), trace_id=trace_id)


@router.get(
    "/admin/tenants/{tenant_id}/incidents",
    response_model=ResponseEnvelope,
)
async def list_incidents(
    tenant_id: TenantPath,
    request: Request,
    principal: AdminPrincipalDep,
    service: IncidentQueryServiceDep,
    statuses: IncidentStatusesQuery = None,
    limit: IncidentListLimit = 50,
    cursor: IncidentCursorQuery = None,
) -> dict:
    """按状态和稳定游标读取租户内有限事故列表。"""
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.list(
        ListIncidentsQuery(
            tenant_id=tenant_id,
            statuses=frozenset(statuses or IncidentStatus),
            limit=limit,
            cursor=cursor,
        )
    )
    return success_response(data=result.to_dict(), trace_id=trace_id)


@router.get(
    "/admin/tenants/{tenant_id}/incidents/{incident_id}",
    response_model=ResponseEnvelope,
)
async def get_incident(
    tenant_id: TenantPath,
    incident_id: IncidentPath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: IncidentQueryServiceDep,
) -> dict:
    """读取租户内单个事故，并返回当前强 ETag。"""
    principal.require_tenant_scope(tenant_id, _READ_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.get(
        GetIncidentQuery(
            tenant_id=tenant_id,
            incident_id=incident_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(data=result.to_dict(), trace_id=trace_id)


@router.post(
    (
        "/admin/tenants/{tenant_id}/incidents/{incident_id}"
        "/resolution"
    ),
    response_model=ResponseEnvelope,
)
async def resolve_incident(
    tenant_id: TenantPath,
    incident_id: IncidentPath,
    payload: ResolveIncidentRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: IncidentResolutionServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedIncidentVersionDep,
) -> dict:
    """人工解决明确版本的事故，并返回新 ETag。"""
    principal.require_tenant_scope(tenant_id, _RESOLVE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.resolve(
        payload.to_command(
            tenant_id=tenant_id,
            incident_id=incident_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(data=result.to_dict(), trace_id=trace_id)


@router.post(
    (
        "/admin/tenants/{tenant_id}/incidents/{incident_id}"
        "/closure"
    ),
    response_model=ResponseEnvelope,
)
async def close_incident(
    tenant_id: TenantPath,
    incident_id: IncidentPath,
    payload: CloseIncidentRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: IncidentResolutionServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_version: ExpectedIncidentVersionDep,
) -> dict:
    """人工关闭明确版本的已解决事故，并返回新 ETag。"""
    principal.require_tenant_scope(tenant_id, _CLOSE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.close(
        payload.to_command(
            tenant_id=tenant_id,
            incident_id=incident_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.version}"'
    return success_response(data=result.to_dict(), trace_id=trace_id)
