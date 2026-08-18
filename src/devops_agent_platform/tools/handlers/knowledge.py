from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from devops_agent_platform.application.services.knowledge_service import (
    KnowledgeQuery,
    KnowledgeService,
)
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry


class KnowledgeSearchHandler:
    def __init__(self, service: KnowledgeService) -> None:
        self._service = service

    async def execute(
        self, payload: Mapping[str, Any], trace_id: str
    ) -> dict[str, Any]:
        del trace_id
        if not isinstance(payload, Mapping):
            raise AppValidationError("knowledge search payload must be an object")
        tenant_id = payload.get("tenant_id")
        service = payload.get("service") or payload.get("service_name")
        if not isinstance(tenant_id, str) or not isinstance(service, str):
            raise AppValidationError("tenant_id and service are required")
        query = KnowledgeQuery(
            service=service,
            alert_summary=str(payload.get("alert_summary", "")),
            error_fingerprint=str(payload.get("error_fingerprint", "")),
            log_keywords=tuple(payload.get("log_keywords", ())),
            trace_errors=tuple(payload.get("trace_errors", ())),
            recent_change_type=str(payload.get("recent_change_type", "")),
        )
        results = await self._service.search(
            tenant_id, query, top_k=int(payload.get("top_k", 5))
        )
        return {
            "source": "historical_knowledge",
            "results": [item.to_dict() for item in results],
            "reference_only": True,
        }


def register_knowledge_search_tool(
    registry: ToolHandlerRegistry,
    handler: KnowledgeSearchHandler,
    *,
    timeout_ms: int = 2000,
) -> ToolDefinition:
    definition = ToolDefinition(
        tool_name="knowledge.search",
        version="v1",
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=timeout_ms,
        permission_tags=("knowledge:read", "tenant:observe"),
    )
    registry.register(definition, handler)
    return definition
