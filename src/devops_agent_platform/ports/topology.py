from __future__ import annotations

from typing import Protocol

from devops_agent_platform.domain.models.topology import (
    DependencyEdge,
    ResourceNode,
    ServiceNode,
    TopologyGraph,
)


class TopologyRepositoryPort(Protocol):
    async def save_service(self, node: ServiceNode) -> None: ...

    async def save_resource(self, node: ResourceNode) -> None: ...

    async def save_edge(self, edge: DependencyEdge) -> None: ...

    async def get_graph(
        self,
        tenant_id: str,
        *,
        environment: str | None = None,
        max_depth: int = 8,
    ) -> TopologyGraph: ...


class TraceTopologySourcePort(Protocol):
    async def dependencies(
        self,
        tenant_id: str,
        *,
        incident_id: str | None = None,
        limit: int = 500,
    ) -> tuple[object, ...]: ...
