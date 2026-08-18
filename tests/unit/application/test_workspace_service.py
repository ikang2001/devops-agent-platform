import pytest

from devops_agent_platform.application.services.workspace_service import (
    WorkspaceService,
)
from devops_agent_platform.domain.exceptions import ResourceNotFound
from devops_agent_platform.domain.models.workspace import (
    InMemoryWorkspaceRepository,
    WorkspaceConfig,
)


@pytest.mark.asyncio
async def test_workspace_configuration_is_tenant_isolated():
    service = WorkspaceService(InMemoryWorkspaceRepository())
    workspace = WorkspaceConfig(
        workspace_id="shop",
        tenant_id="tenant-a",
        name="MiniShop",
        prometheus_target="http://prometheus:9090",
        loki_target="http://loki:3100",
        tempo_target="http://tempo:3200",
        allowed_tools=("metrics.query@v1",),
    )
    await service.create_or_update(workspace)
    assert await service.get("tenant-a", "shop") == workspace
    with pytest.raises(ResourceNotFound):
        await service.get("tenant-b", "shop")
