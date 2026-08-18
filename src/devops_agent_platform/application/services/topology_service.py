from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from devops_agent_platform.domain.exceptions import AppValidationError, ConflictError
from devops_agent_platform.domain.models.topology import (
    DependencyEdge,
    ResourceNode,
    ServiceNode,
    TopologyGraph,
    TopologySource,
    TraceDependency,
)
from devops_agent_platform.ports.topology import TopologyRepositoryPort


@dataclass(frozen=True)
class BlastRadiusResult:
    root_node: str
    directly_affected_services: tuple[str, ...]
    indirectly_affected_services: tuple[str, ...]
    critical_dependencies: tuple[str, ...]
    impact_summary: str
    scores: tuple[tuple[str, float], ...]
    causal_chain: tuple[tuple[str, str], ...]

    @property
    def affected_services(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (*self.directly_affected_services, *self.indirectly_affected_services)
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_node": self.root_node,
            "directly_affected_services": list(self.directly_affected_services),
            "indirectly_affected_services": list(self.indirectly_affected_services),
            "critical_dependencies": list(self.critical_dependencies),
            "impact_summary": self.impact_summary,
            "scores": {key: value for key, value in self.scores},
            "causal_chain": [
                {"from": source, "to": target} for source, target in self.causal_chain
            ],
            "affected_services": list(self.affected_services),
        }


class TopologyService:
    def __init__(
        self,
        repository: TopologyRepositoryPort,
        identifier_generator: object | None = None,
    ) -> None:
        self._repository = repository
        self._identifier_generator = identifier_generator

    async def register_static_service(self, node: ServiceNode) -> None:
        if node.source is not TopologySource.STATIC:
            raise AppValidationError("static services must use STATIC source")
        await self._repository.save_service(node)

    async def register_resource(self, node: ResourceNode) -> None:
        await self._repository.save_resource(node)

    async def register_dependency(
        self, edge: DependencyEdge, *, reject_cycles: bool = True
    ) -> None:
        graph = await self._repository.get_graph(edge.tenant_id, max_depth=32)
        if (
            edge.source_node_id not in graph.node_by_id
            or edge.target_node_id not in graph.node_by_id
        ):
            raise ConflictError("topology dependency references unknown node")
        if reject_cycles and graph.would_create_cycle(
            edge.source_node_id, edge.target_node_id
        ):
            raise ConflictError("topology dependency would create a cycle")
        await self._repository.save_edge(edge)

    async def derive_from_traces(
        self, dependencies: tuple[TraceDependency, ...] | list[TraceDependency]
    ) -> int:
        count = 0
        for dependency in dependencies:
            graph = await self._repository.get_graph(
                dependency.tenant_id, environment=dependency.environment, max_depth=32
            )
            for service_name in (dependency.caller_service, dependency.callee_service):
                node_id = f"service:{service_name}"
                if node_id not in graph.node_by_id:
                    await self._repository.save_service(
                        ServiceNode(
                            node_id=node_id,
                            tenant_id=dependency.tenant_id,
                            service_name=service_name,
                            environment=dependency.environment,
                            source=TopologySource.TRACE,
                            confidence=dependency.confidence,
                        )
                    )
                    graph = await self._repository.get_graph(
                        dependency.tenant_id,
                        environment=dependency.environment,
                        max_depth=32,
                    )
            edge = DependencyEdge(
                edge_id=f"trace:{dependency.tenant_id}:{dependency.caller_service}:{dependency.callee_service}",
                tenant_id=dependency.tenant_id,
                source_node_id=f"service:{dependency.caller_service}",
                target_node_id=f"service:{dependency.callee_service}",
                source=TopologySource.TRACE,
                confidence=dependency.confidence,
                metadata=(("trace_id", dependency.trace_id),),
            )
            if edge.key not in {item.key for item in graph.edges}:
                await self.register_dependency(edge, reject_cycles=False)
                count += 1
        return count

    async def query(
        self, tenant_id: str, *, environment: str | None = None, max_depth: int = 8
    ) -> TopologyGraph:
        return await self._repository.get_graph(
            tenant_id, environment=environment, max_depth=max_depth
        )


class BlastRadiusService:
    """基于有向依赖图计算影响面；不使用 LLM，保证同一图输入得到同一结果。"""

    async def calculate(
        self,
        graph: TopologyGraph,
        suspected_root_node: str,
        *,
        max_depth: int | None = None,
    ) -> BlastRadiusResult:
        if suspected_root_node not in graph.node_by_id:
            raise AppValidationError("suspected_root_node is not present in topology")
        depth_limit = max_depth if max_depth is not None else graph.max_depth
        if not isinstance(depth_limit, int) or not 1 <= depth_limit <= graph.max_depth:
            raise AppValidationError("max_depth exceeds topology budget")
        queue: deque[tuple[str, int]] = deque([(suspected_root_node, 0)])
        visited: dict[str, int] = {suspected_root_node: 0}
        edges_by_node: dict[str, tuple[DependencyEdge, ...]] = {}
        for node_id in graph.node_by_id:
            edges_by_node[node_id] = graph.outgoing(node_id)
        causal: list[tuple[str, str]] = []
        while queue:
            current, depth = queue.popleft()
            if depth >= depth_limit:
                continue
            for edge in edges_by_node.get(current, ()):
                if edge.target_node_id not in visited:
                    visited[edge.target_node_id] = depth + 1
                    queue.append((edge.target_node_id, depth + 1))
                    causal.append((edge.source_node_id, edge.target_node_id))
        direct = sorted(
            node
            for node, depth in visited.items()
            if depth == 1 and node != suspected_root_node
        )
        indirect = sorted(node for node, depth in visited.items() if depth > 1)
        scores = tuple(
            sorted(
                (node, round(self._score(graph, node, visited[node]), 6))
                for node in (*direct, *indirect)
            )
        )
        critical = tuple(sorted(node for node, score in scores if score >= 0.5))
        return BlastRadiusResult(
            root_node=suspected_root_node,
            directly_affected_services=tuple(direct),
            indirectly_affected_services=tuple(indirect),
            critical_dependencies=critical,
            impact_summary=(
                f"{len(direct) + len(indirect)} nodes affected by {suspected_root_node}"
            ),
            scores=scores,
            causal_chain=tuple(causal),
        )

    @staticmethod
    def _score(graph: TopologyGraph, node_id: str, depth: int) -> float:
        edge = next(
            (item for item in graph.edges if item.target_node_id == node_id), None
        )
        confidence = edge.confidence if edge is not None else 0.0
        return confidence / max(depth, 1)


def build_service_node(
    tenant_id: str,
    service_name: str,
    *,
    environment: str = "default",
    source: TopologySource = TopologySource.STATIC,
    confidence: float = 1.0,
) -> ServiceNode:
    return ServiceNode(
        node_id=f"service:{service_name}",
        tenant_id=tenant_id,
        service_name=service_name,
        environment=environment,
        source=source,
        confidence=confidence,
    )


def build_dependency_edge(
    tenant_id: str,
    source_service: str,
    target_service: str,
    *,
    source: TopologySource = TopologySource.STATIC,
    confidence: float = 1.0,
) -> DependencyEdge:
    return DependencyEdge(
        edge_id=f"edge:{tenant_id}:{source_service}:{target_service}",
        tenant_id=tenant_id,
        source_node_id=f"service:{source_service}",
        target_node_id=f"service:{target_service}",
        source=source,
        confidence=confidence,
    )
