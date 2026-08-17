import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from devops_agent_platform.application.exceptions import RunbookSourceError
from devops_agent_platform.domain.enums import RunbookStatus, ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.runbook import Runbook
from devops_agent_platform.ports.observability import (
    ObservabilityTargetResolverPort,
)
from devops_agent_platform.ports.runbooks import RunbookSearchPort
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry


@dataclass(frozen=True)
class RunbookRetrievalHandlerConfig:
    """Runbook 工具的查询和输出容量边界。"""

    max_requested_results: int = 10
    max_output_bytes: int = 60 * 1024

    def __post_init__(self) -> None:
        """拒绝无界查询以及超过 Evidence 容量的输出配置。"""
        if (
            isinstance(self.max_requested_results, bool)
            or not isinstance(self.max_requested_results, int)
            or not 1 <= self.max_requested_results <= 50
        ):
            raise AppValidationError("max_requested_results must be between 1 and 50")
        if (
            isinstance(self.max_output_bytes, bool)
            or not isinstance(self.max_output_bytes, int)
            or not 48 * 1024 <= self.max_output_bytes <= 64 * 1024
        ):
            raise AppValidationError("max_output_bytes must be between 49152 and 65536")


class RunbookRetrievalHandler:
    """按可信事故服务检索已发布 Runbook，不执行其中任何步骤。"""

    def __init__(
        self,
        target_resolver: ObservabilityTargetResolverPort,
        search: RunbookSearchPort,
        config: RunbookRetrievalHandlerConfig | None = None,
    ) -> None:
        """创建无状态 Handler，数据库会话由检索适配器按调用管理。"""
        if not callable(getattr(target_resolver, "resolve", None)):
            raise AppValidationError("target_resolver must provide resolve")
        if not callable(getattr(search, "search", None)):
            raise AppValidationError("search must provide search")
        if config is not None and not isinstance(
            config,
            RunbookRetrievalHandlerConfig,
        ):
            raise AppValidationError("config must be a RunbookRetrievalHandlerConfig")
        self._target_resolver = target_resolver
        self._search = search
        self._config = config or RunbookRetrievalHandlerConfig()

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        """解析可信服务目标并返回确定性、有界的发布手册列表。"""
        del trace_id
        request = self._parse_payload(payload)
        target = await self._target_resolver.resolve(
            request["tenant_id"],
            request["incident_id"],
        )
        search_result = await self._search.search(
            target.tenant_id,
            target.service_name,
            request["max_results"],
        )
        self._validate_search_result(
            search_result.items,
            target.tenant_id,
            target.service_name,
            request["max_results"],
        )
        titles = ", ".join(runbook.title[:256] for runbook in search_result.items)
        summary = f"Retrieved published runbooks for service {target.service_name}"
        if titles:
            summary += f"; titles={titles[:1024]}"
        response: dict[str, Any] = {
            "source": "runbook_catalog",
            "target": {
                "tenant_id": target.tenant_id,
                "incident_id": target.incident_id,
                "service_name": target.service_name,
            },
            "summary": summary,
            "runbooks": [],
            "requested_max_results": request["max_results"],
            "possibly_truncated": search_result.possibly_truncated,
        }
        for runbook in search_result.items:
            response["runbooks"].append(
                self._serialize_runbook(runbook, target.service_name)
            )
            if self._encoded_size(response) > self._config.max_output_bytes:
                response["runbooks"].pop()
                response["possibly_truncated"] = True
                break
        response["returned_results"] = len(response["runbooks"])
        if self._encoded_size(response) > self._config.max_output_bytes:
            raise RunbookSourceError("Runbook response exceeds the output boundary")
        return response

    def _parse_payload(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """只接受工作流身份字段和有限结果数量。"""
        if not isinstance(payload, Mapping):
            raise AppValidationError("runbook payload must be a mapping")
        allowed_fields = {
            "tenant_id",
            "incident_id",
            "max_results",
            "execution_attempt",
            "operator_id",
            "plan_id",
            "plan_version",
            "step_id",
            "trace_id",
            "worker_id",
            "workflow_run_id",
        }
        unknown_fields = sorted(set(payload).difference(allowed_fields))
        if unknown_fields:
            raise AppValidationError(
                "runbook payload contains unsupported fields: "
                f"{', '.join(unknown_fields)}"
            )
        tenant_id = self._required_text(payload, "tenant_id", 128)
        incident_id = self._required_text(payload, "incident_id", 64)
        max_results = payload.get("max_results")
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= self._config.max_requested_results
        ):
            raise AppValidationError(
                "max_results must be between 1 and "
                f"{self._config.max_requested_results}"
            )
        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "max_results": max_results,
        }

    @staticmethod
    def _validate_search_result(
        items: tuple[Runbook, ...],
        tenant_id: str,
        service_name: str,
        limit: int,
    ) -> None:
        """阻止错误检索适配器泄露跨租户、草稿或无关服务手册。"""
        if len(items) > limit:
            raise RunbookSourceError("Runbook source exceeded the requested limit")
        runbook_ids = [item.runbook_id for item in items]
        if len(runbook_ids) != len(set(runbook_ids)):
            raise RunbookSourceError("Runbook source returned duplicate records")
        for item in items:
            if (
                item.tenant_id != tenant_id
                or item.service_name not in {service_name, "*"}
                or item.status is not RunbookStatus.PUBLISHED
            ):
                raise RunbookSourceError("Runbook source crossed a trust boundary")

    @staticmethod
    def _serialize_runbook(
        runbook: Runbook,
        service_name: str,
    ) -> dict[str, Any]:
        """仅暴露审核内容，不返回内部优先级等管理字段。"""
        return {
            "runbook_id": runbook.runbook_id,
            "runbook_key": runbook.runbook_key,
            "version": runbook.version,
            "scope": ("SERVICE" if runbook.service_name == service_name else "TENANT"),
            "title": runbook.title,
            "summary": runbook.summary,
            "steps": list(runbook.steps),
            "tags": list(runbook.tags),
            "updated_at": runbook.updated_at.isoformat(),
        }

    @staticmethod
    def _required_text(
        payload: Mapping[str, Any],
        field_name: str,
        maximum: int,
    ) -> str:
        """校验工作流注入的租户和事故身份。"""
        value = payload.get(field_name)
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")
        return value

    @staticmethod
    def _encoded_size(value: object) -> int:
        """按 ToolExecutor 使用的 JSON 语义计算实际 UTF-8 大小。"""
        try:
            return len(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise RunbookSourceError(
                "Runbook response is not JSON serializable"
            ) from exc


def register_runbook_retrieval_tool(
    registry: ToolHandlerRegistry,
    handler: RunbookRetrievalHandler,
    *,
    timeout_ms: int = 2000,
) -> ToolDefinition:
    """把 runbooks.retrieve@v1 定义和处理器绑定到注册表。"""
    definition = ToolDefinition(
        tool_name="runbooks.retrieve",
        version="v1",
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=timeout_ms,
        permission_tags=("runbooks:read", "tenant:observe"),
    )
    registry.register(definition, handler)
    return definition
