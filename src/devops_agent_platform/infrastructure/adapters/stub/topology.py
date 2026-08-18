from __future__ import annotations

from datetime import datetime

from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.domain.models.topology import (
    DependencyEdge,
    ResourceNode,
    ServiceNode,
    TopologyGraph,
)


class InMemoryTopologyRepository:
    """本地测试与 MiniShop 演练使用的租户隔离拓扑存储。"""

    def __init__(self) -> None:
        self._services: dict[tuple[str, str], ServiceNode] = {}
        self._resources: dict[tuple[str, str], ResourceNode] = {}
        self._edges: dict[tuple[str, tuple[str, str, str]], DependencyEdge] = {}

    async def save_service(self, node: ServiceNode) -> None:
        self._check_tenant(node.tenant_id)
        self._services[(node.tenant_id, node.node_id)] = node

    async def save_resource(self, node: ResourceNode) -> None:
        self._check_tenant(node.tenant_id)
        self._resources[(node.tenant_id, node.node_id)] = node

    async def save_edge(self, edge: DependencyEdge) -> None:
        self._check_tenant(edge.tenant_id)
        key = (edge.tenant_id, edge.key)
        existing = self._edges.get(key)
        if existing is not None and existing.edge_id != edge.edge_id:
            raise ConflictError("duplicate topology edge")
        self._edges[key] = edge

    async def get_graph(
        self,
        tenant_id: str,
        *,
        environment: str | None = None,
        max_depth: int = 8,
    ) -> TopologyGraph:
        self._check_tenant(tenant_id)
        now = datetime.now().astimezone()
        nodes = tuple(
            node
            for (node_tenant, _), node in (
                *self._services.items(),
                *self._resources.items(),
            )
            if node_tenant == tenant_id
            and (environment is None or node.environment == environment)
            and not node.is_expired(now)
        )
        node_ids = {node.node_id for node in nodes}
        edges = tuple(
            edge
            for (edge_tenant, _), edge in self._edges.items()
            if edge_tenant == tenant_id
            and edge.source_node_id in node_ids
            and edge.target_node_id in node_ids
            and not edge.is_expired(now)
        )
        return TopologyGraph(
            tenant_id=tenant_id,
            nodes=tuple(sorted(nodes, key=lambda node: node.node_id)),
            edges=tuple(sorted(edges, key=lambda edge: edge.key)),
            max_depth=max_depth,
        )

    @staticmethod
    def _check_tenant(tenant_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
