import re
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Request,
    Response,
)

from devops_agent_platform.application.commands.runbooks import (
    PublishRunbookCommand,
)
from devops_agent_platform.application.exceptions import (
    PreconditionRequiredError,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.runbook_admin_service import (
    RunbookAdminService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_runbook_admin_service,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.interfaces.http.dto import (
    SaveRunbookDraftRequest,
)
from devops_agent_platform.interfaces.http.header_params import (
    IdempotencyKeyHeader,
)
from devops_agent_platform.interfaces.http.path_params import (
    RunbookKeyPath,
    RunbookVersionPath,
    TenantPath,
)
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["runbooks"])
_ETAG_PATTERN = re.compile(r'^"([0-9]+)"$')
_WRITE_SCOPE = "runbooks:write"

AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
RunbookAdminServiceDep = Annotated[
    RunbookAdminService,
    Depends(get_runbook_admin_service),
]
IfMatchHeader = Annotated[
    str | None,
    Header(alias="If-Match", max_length=32),
]


def get_expected_revision(
    if_match: IfMatchHeader = None,
) -> int:
    """把强 ETag 条件头转换为非负 Runbook 聚合修订号。"""
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


ExpectedRevisionDep = Annotated[int, Depends(get_expected_revision)]


@router.put(
    (
        "/admin/tenants/{tenant_id}/runbooks/{runbook_key}"
        "/versions/{version}/draft"
    ),
    response_model=ResponseEnvelope,
)
async def save_runbook_draft(
    tenant_id: TenantPath,
    runbook_key: RunbookKeyPath,
    version: RunbookVersionPath,
    payload: SaveRunbookDraftRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: RunbookAdminServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_revision: ExpectedRevisionDep,
) -> dict:
    """创建或更新明确业务版本的 Runbook 草稿。"""
    principal.require_tenant_scope(tenant_id, _WRITE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.save_draft(
        payload.to_command(
            tenant_id=tenant_id,
            runbook_key=runbook_key,
            version=version,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return success_response(result.to_dict(), trace_id)


@router.post(
    (
        "/admin/tenants/{tenant_id}/runbooks/{runbook_key}"
        "/versions/{version}/publish"
    ),
    response_model=ResponseEnvelope,
)
async def publish_runbook(
    tenant_id: TenantPath,
    runbook_key: RunbookKeyPath,
    version: RunbookVersionPath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: RunbookAdminServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_revision: ExpectedRevisionDep,
) -> dict:
    """发布当前活动草稿，不接受任何正文替换。"""
    principal.require_tenant_scope(tenant_id, _WRITE_SCOPE)
    trace_id = get_trace_id(request)
    result = await service.publish(
        PublishRunbookCommand(
            tenant_id=tenant_id,
            runbook_key=runbook_key,
            version=version,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return success_response(result.to_dict(), trace_id)
