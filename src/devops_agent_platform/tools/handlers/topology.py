from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from devops_agent_platform.application.services.topology_service import TopologyService
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry


class TopologyQueryHandler:
    def __init__(self, service: TopologyService) -> None:
        self._service = service

    async def execute(
        self, payload: Mapping[str, Any], trace_id: str
    ) -> dict[str, Any]:
        del trace_id
        if not isinstance(payload, Mapping):
            raise AppValidationError("topology query payload must be an object")
        tenant_id = payload.get("tenant_id")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise AppValidationError("tenant_id is required")
        graph = await self._service.query(
            tenant_id,
            environment=payload.get("environment"),
            max_depth=payload.get("max_depth", 8),
        )
        return {
            "source": "topology",
            "tenant_id": tenant_id,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "kind": node.kind.value,
                    "name": getattr(
                        node, "service_name", getattr(node, "resource_name", "")
                    ),
                    "source": node.source.value,
                    "confidence": node.confidence,
                }
                for node in graph.nodes
            ],
            "edges": [
                {
                    "edge_id": edge.edge_id,
                    "from": edge.source_node_id,
                    "to": edge.target_node_id,
                    "relation": edge.relation,
                    "source": edge.source.value,
                    "confidence": edge.confidence,
                }
                for edge in graph.edges
            ],
            "max_depth": graph.max_depth,
        }


def register_topology_query_tool(
    registry: ToolHandlerRegistry,
    handler: TopologyQueryHandler,
    *,
    timeout_ms: int = 2000,
) -> ToolDefinition:
    definition = ToolDefinition(
        tool_name="topology.query",
        version="v1",
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=timeout_ms,
        permission_tags=("topology:read", "tenant:observe"),
    )
    registry.register(definition, handler)
    return definition
