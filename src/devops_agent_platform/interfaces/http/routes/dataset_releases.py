from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from devops_agent_platform.application.commands.dataset_releases import (
    PublishDatasetReleaseCommand,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.dataset_release_service import (
    DatasetReleaseService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_administrator_principal,
    get_dataset_release_service,
)
from devops_agent_platform.interfaces.http.dto import (
    CreateDatasetReleaseRequest,
    ReviewDatasetReleaseRequest,
)
from devops_agent_platform.interfaces.http.header_params import IdempotencyKeyHeader
from devops_agent_platform.interfaces.http.path_params import (
    DatasetReleasePath,
    TenantPath,
)
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)
from devops_agent_platform.interfaces.http.routes.workspaces import ExpectedRevisionDep

router = APIRouter(tags=["dataset-releases"])

AdminPrincipalDep = Annotated[
    AdministratorPrincipal,
    Depends(get_administrator_principal),
]
DatasetReleaseServiceDep = Annotated[
    DatasetReleaseService,
    Depends(get_dataset_release_service),
]


@router.put(
    "/admin/tenants/{tenant_id}/dataset-releases/{release_id}",
    response_model=ResponseEnvelope,
)
async def create_dataset_release(
    tenant_id: TenantPath,
    release_id: DatasetReleasePath,
    payload: CreateDatasetReleaseRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: DatasetReleaseServiceDep,
    idempotency_key: IdempotencyKeyHeader,
) -> dict:
    principal.require_tenant_scope(tenant_id, "datasets:curate")
    trace_id = get_trace_id(request)
    result = await service.create(
        payload.to_command(
            tenant_id=tenant_id,
            release_id=release_id,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.release.revision}"'
    return success_response(result.to_dict(), trace_id)


@router.post(
    "/admin/tenants/{tenant_id}/dataset-releases/{release_id}/reviews",
    response_model=ResponseEnvelope,
)
async def review_dataset_release(
    tenant_id: TenantPath,
    release_id: DatasetReleasePath,
    payload: ReviewDatasetReleaseRequest,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: DatasetReleaseServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_revision: ExpectedRevisionDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, "datasets:review")
    trace_id = get_trace_id(request)
    result = await service.review(
        payload.to_command(
            tenant_id=tenant_id,
            release_id=release_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.release.revision}"'
    return success_response(result.to_dict(), trace_id)


@router.post(
    "/admin/tenants/{tenant_id}/dataset-releases/{release_id}/publish",
    response_model=ResponseEnvelope,
)
async def publish_dataset_release(
    tenant_id: TenantPath,
    release_id: DatasetReleasePath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: DatasetReleaseServiceDep,
    idempotency_key: IdempotencyKeyHeader,
    expected_revision: ExpectedRevisionDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, "datasets:publish")
    trace_id = get_trace_id(request)
    result = await service.publish(
        PublishDatasetReleaseCommand(
            tenant_id=tenant_id,
            release_id=release_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            requested_by=principal.admin_id,
            trace_id=trace_id,
        )
    )
    response.headers["ETag"] = f'"{result.release.revision}"'
    return success_response(result.to_dict(), trace_id)


@router.get(
    "/admin/tenants/{tenant_id}/dataset-releases/{release_id}",
    response_model=ResponseEnvelope,
)
async def get_dataset_release(
    tenant_id: TenantPath,
    release_id: DatasetReleasePath,
    request: Request,
    response: Response,
    principal: AdminPrincipalDep,
    service: DatasetReleaseServiceDep,
) -> dict:
    principal.require_tenant_scope(tenant_id, "datasets:read")
    trace_id = get_trace_id(request)
    result = await service.get(tenant_id, release_id, trace_id)
    response.headers["ETag"] = f'"{result.release.revision}"'
    return success_response(result.to_dict(), trace_id)
