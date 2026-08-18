import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from devops_agent_platform.application.exceptions import ChangeSourceError
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.change_event import ChangeEvent
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]


@dataclass(frozen=True)
class ChangeEventsQueryHandlerConfig:
    """由服务端固定的事故前后窗口和结果容量边界。"""

    minutes_before_incident: int = 30
    minutes_after_incident: int = 10
    max_requested_results: int = 50
    max_output_bytes: int = 60 * 1024

    def __post_init__(self) -> None:
        self._validate_positive_int(
            "minutes_before_incident",
            self.minutes_before_incident,
            120,
        )
        self._validate_positive_int(
            "minutes_after_incident",
            self.minutes_after_incident,
            30,
        )
        self._validate_positive_int(
            "max_requested_results",
            self.max_requested_results,
            99,
        )
        if (
            isinstance(self.max_output_bytes, bool)
            or not isinstance(self.max_output_bytes, int)
            or not 48 * 1024 <= self.max_output_bytes <= 64 * 1024
        ):
            raise AppValidationError("max_output_bytes must be between 49152 and 65536")

    @staticmethod
    def _validate_positive_int(
        field_name: str,
        value: int,
        maximum: int,
    ) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise AppValidationError(f"{field_name} must be between 1 and {maximum}")


class ChangeEventsQueryHandler:
    """从可信 Incident 派生服务和时间窗口的只读变更查询工具。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        config: ChangeEventsQueryHandlerConfig | None = None,
    ) -> None:
        if not callable(unit_of_work_factory):
            raise AppValidationError("unit_of_work_factory must be callable")
        if config is not None and not isinstance(
            config,
            ChangeEventsQueryHandlerConfig,
        ):
            raise AppValidationError("config must be a ChangeEventsQueryHandlerConfig")
        self._unit_of_work_factory = unit_of_work_factory
        self._config = config or ChangeEventsQueryHandlerConfig()

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        """查询事故窗口内的变更；没有变更是正常空结果。"""
        del trace_id
        request = self._parse_payload(payload)
        async with self._unit_of_work_factory() as unit_of_work:
            incident = await unit_of_work.incidents.get_by_id(
                request["incident_id"],
                request["tenant_id"],
            )
            if incident is None:
                raise ResourceNotFound("Incident change target not found")
            started_at_from, started_at_to = self._window_for_incident(incident)
            events = await unit_of_work.change_events.list_in_time_window(
                incident.tenant_id,
                incident.service_name,
                started_at_from,
                started_at_to,
                limit=request["max_results"] + 1,
            )

        self._validate_results(
            events,
            incident,
            started_at_from,
            started_at_to,
            request["max_results"] + 1,
        )
        return self._build_response(
            incident,
            events,
            started_at_from,
            started_at_to,
            request["max_results"],
        )

    def _parse_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """只接受工作流身份和结果上限，不接受服务或窗口边界。"""
        if not isinstance(payload, Mapping):
            raise AppValidationError("change query payload must be a mapping")
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
                "change query payload contains unsupported fields: "
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

    def _window_for_incident(
        self,
        incident: Incident,
    ) -> tuple[datetime, datetime]:
        return (
            incident.created_at
            - timedelta(minutes=self._config.minutes_before_incident),
            incident.created_at
            + timedelta(minutes=self._config.minutes_after_incident),
        )

    @staticmethod
    def _validate_results(
        events: list[ChangeEvent],
        incident: Incident,
        started_at_from: datetime,
        started_at_to: datetime,
        limit: int,
    ) -> None:
        """防御错误适配器返回越界、乱序、重复或跨租户数据。"""
        if len(events) > limit:
            raise ChangeSourceError("Change source exceeded the requested limit")
        event_ids = [event.change_event_id for event in events]
        if len(event_ids) != len(set(event_ids)):
            raise ChangeSourceError("Change source returned duplicate records")
        expected_order = sorted(
            events,
            key=lambda event: (event.started_at, event.change_event_id),
            reverse=True,
        )
        if events != expected_order:
            raise ChangeSourceError("Change source returned unstable ordering")
        for event in events:
            if (
                event.tenant_id != incident.tenant_id
                or event.service_name != incident.service_name
                or not started_at_from <= event.started_at <= started_at_to
            ):
                raise ChangeSourceError("Change source crossed a trust boundary")

    def _build_response(
        self,
        incident: Incident,
        events: list[ChangeEvent],
        started_at_from: datetime,
        started_at_to: datetime,
        max_results: int,
    ) -> dict[str, Any]:
        selected = events[:max_results]
        response: dict[str, Any] = {
            "source": "change_event_store",
            "target": {
                "tenant_id": incident.tenant_id,
                "incident_id": incident.incident_id,
                "service_name": incident.service_name,
            },
            "window": {
                "started_at_from": started_at_from.isoformat(),
                "started_at_to": started_at_to.isoformat(),
                "incident_started_at": incident.created_at.isoformat(),
            },
            "summary": self._build_summary(incident, selected),
            "changes": [],
            "requested_max_results": max_results,
            "possibly_truncated": len(events) > max_results,
        }
        for event in selected:
            response["changes"].append(self._serialize(event, incident.created_at))
            if self._encoded_size(response) > self._config.max_output_bytes:
                response["changes"].pop()
                response["possibly_truncated"] = True
                break
        response["returned_results"] = len(response["changes"])
        if self._encoded_size(response) > self._config.max_output_bytes:
            raise ChangeSourceError("Change response exceeds the output boundary")
        return response

    @staticmethod
    def _build_summary(
        incident: Incident,
        events: list[ChangeEvent],
    ) -> str:
        """仅投影稳定变更字段，让模型能看到时间线但接触不到 metadata。"""
        if not events:
            return f"No change events found for service {incident.service_name}"
        latest = events[0]
        before = latest.version_before or "unknown"
        after = latest.version_after or "unknown"
        offset = round(
            (latest.started_at - incident.created_at).total_seconds() / 60,
            3,
        )
        return (
            f"Collected {len(events)} change events for service "
            f"{incident.service_name}; latest={latest.change_type.value} "
            f"{latest.resource_id}:{before}->{after} "
            f"status={latest.status.value}; minutes_from_incident={offset}"
        )

    @staticmethod
    def _serialize(
        event: ChangeEvent,
        incident_started_at: datetime,
    ) -> dict[str, Any]:
        return {
            "change_event_id": event.change_event_id,
            "source": event.source,
            "service_name": event.service_name,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "change_type": event.change_type.value,
            "status": event.status.value,
            "version_before": event.version_before,
            "version_after": event.version_after,
            "summary": event.summary,
            "started_at": event.started_at.isoformat(),
            "completed_at": (
                event.completed_at.isoformat()
                if event.completed_at is not None
                else None
            ),
            "minutes_from_incident": round(
                (event.started_at - incident_started_at).total_seconds() / 60,
                3,
            ),
        }

    @staticmethod
    def _required_text(
        payload: Mapping[str, Any],
        field_name: str,
        maximum: int,
    ) -> str:
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
            raise ChangeSourceError("Change response is not JSON serializable") from exc


def register_change_events_query_tool(
    registry: ToolHandlerRegistry,
    handler: ChangeEventsQueryHandler,
    *,
    timeout_ms: int = 2000,
) -> ToolDefinition:
    """注册只读、租户隔离的 changes.query@v1 工具。"""
    definition = ToolDefinition(
        tool_name="changes.query",
        version="v1",
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=timeout_ms,
        permission_tags=("changes:read", "tenant:observe"),
    )
    registry.register(definition, handler)
    return definition
