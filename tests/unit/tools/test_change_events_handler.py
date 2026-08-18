from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.agent.controlled_workflow import ControlledAgentWorkflow
from devops_agent_platform.application.exceptions import ChangeSourceError
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    ChangeEventStatus,
    ChangeType,
    EvidenceType,
    IncidentStatus,
    ToolRiskLevel,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    PermissionDenied,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.change_event import (
    ChangeEvent,
    build_change_metadata,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.handlers.change_events import (
    ChangeEventsQueryHandler,
    register_change_events_query_tool,
)
from devops_agent_platform.tools.permission import (
    ToolPermissionChecker,
    ToolPermissionGrant,
)

INCIDENT_TIME = datetime(2026, 8, 17, 14, 6, tzinfo=UTC)


def build_incident() -> Incident:
    return Incident(
        incident_id="inc_001",
        tenant_id="tenant_001",
        service_name="payment-service",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.OPEN,
        title="Checkout failures",
        created_at=INCIDENT_TIME,
        updated_at=INCIDENT_TIME,
    )


def build_change_event(
    change_event_id: str,
    started_at: datetime,
    *,
    tenant_id: str = "tenant_001",
    service_name: str = "payment-service",
) -> ChangeEvent:
    return ChangeEvent(
        change_event_id=change_event_id,
        tenant_id=tenant_id,
        source="argocd",
        external_event_id=f"external_{change_event_id}",
        service_name=service_name,
        resource_type="deployment",
        resource_id="payment-service",
        change_type=ChangeType.DEPLOYMENT,
        status=ChangeEventStatus.SUCCEEDED,
        version_before="v1",
        version_after="v2",
        operator_id="deployment-bot",
        summary="payment-service upgraded from v1 to v2",
        metadata_json=build_change_metadata({"cluster": "minishop"}),
        started_at=started_at,
        completed_at=started_at + timedelta(minutes=1),
        created_at=started_at + timedelta(minutes=1),
        request_hash="c" * 64,
    )


class FakeIncidentRepository:
    def __init__(self, incident: Incident | None) -> None:
        self.incident = incident
        self.calls: list[tuple[str, str]] = []

    async def get_by_id(
        self,
        incident_id: str,
        tenant_id: str,
    ) -> Incident | None:
        self.calls.append((incident_id, tenant_id))
        return self.incident


class FakeChangeEventRepository:
    def __init__(self, events: list[ChangeEvent]) -> None:
        self.events = events
        self.calls: list[tuple[object, ...]] = []

    async def list_in_time_window(
        self,
        tenant_id: str,
        service_name: str,
        started_at_from: datetime,
        started_at_to: datetime,
        *,
        limit: int,
    ) -> list[ChangeEvent]:
        self.calls.append(
            (
                tenant_id,
                service_name,
                started_at_from,
                started_at_to,
                limit,
            )
        )
        return self.events[:limit]


class FakeUnitOfWork:
    def __init__(
        self,
        incident: Incident | None,
        events: list[ChangeEvent],
    ) -> None:
        self.incidents = FakeIncidentRepository(incident)
        self.change_events = FakeChangeEventRepository(events)

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


def build_handler(
    events: list[ChangeEvent],
    *,
    incident: Incident | None = None,
) -> tuple[ChangeEventsQueryHandler, FakeUnitOfWork]:
    unit_of_work = FakeUnitOfWork(incident or build_incident(), events)
    handler = ChangeEventsQueryHandler(
        lambda: unit_of_work,  # type: ignore[arg-type]
    )
    return handler, unit_of_work


def valid_payload(max_results: int = 10) -> dict[str, object]:
    return {
        "tenant_id": "tenant_001",
        "incident_id": "inc_001",
        "max_results": max_results,
    }


async def test_query_derives_service_and_fixed_window_from_incident() -> None:
    handler, unit_of_work = build_handler(
        [
            build_change_event(
                "chg_after",
                INCIDENT_TIME + timedelta(minutes=2),
            ),
            build_change_event(
                "chg_before",
                INCIDENT_TIME - timedelta(minutes=4),
            ),
        ]
    )

    result = await handler.execute(valid_payload(), "trc_001")

    assert unit_of_work.incidents.calls == [("inc_001", "tenant_001")]
    assert unit_of_work.change_events.calls == [
        (
            "tenant_001",
            "payment-service",
            INCIDENT_TIME - timedelta(minutes=30),
            INCIDENT_TIME + timedelta(minutes=10),
            11,
        )
    ]
    assert result["target"]["service_name"] == "payment-service"
    assert [item["change_event_id"] for item in result["changes"]] == [
        "chg_after",
        "chg_before",
    ]
    assert [item["minutes_from_incident"] for item in result["changes"]] == [
        2.0,
        -4.0,
    ]
    assert "latest=DEPLOYMENT" in result["summary"]
    assert "payment-service:v1->v2" in result["summary"]


async def test_empty_change_window_is_a_successful_empty_result() -> None:
    handler, _ = build_handler([])

    result = await handler.execute(valid_payload(), "trc_001")

    assert result["changes"] == []
    assert result["returned_results"] == 0
    assert result["possibly_truncated"] is False
    assert "No change events" in result["summary"]


async def test_query_uses_limit_plus_one_to_report_truncation() -> None:
    events = [
        build_change_event(
            f"chg_{index}",
            INCIDENT_TIME - timedelta(minutes=index),
        )
        for index in range(3)
    ]
    handler, unit_of_work = build_handler(events)

    result = await handler.execute(valid_payload(max_results=2), "trc_001")

    assert unit_of_work.change_events.calls[0][-1] == 3
    assert result["returned_results"] == 2
    assert result["possibly_truncated"] is True


@pytest.mark.parametrize(
    "unsupported_field",
    ["service_name", "started_at_from", "started_at_to", "window_minutes"],
)
async def test_query_rejects_llm_controlled_target_or_time_fields(
    unsupported_field: str,
) -> None:
    handler, _ = build_handler([])
    payload = valid_payload()
    payload[unsupported_field] = "attacker-controlled"

    with pytest.raises(AppValidationError, match="unsupported fields"):
        await handler.execute(payload, "trc_001")


async def test_missing_incident_does_not_fall_back_to_arbitrary_service() -> None:
    unit_of_work = FakeUnitOfWork(None, [])
    handler = ChangeEventsQueryHandler(
        lambda: unit_of_work,  # type: ignore[arg-type]
    )

    with pytest.raises(ResourceNotFound, match="change target"):
        await handler.execute(valid_payload(), "trc_001")


async def test_cross_tenant_change_result_is_rejected() -> None:
    handler, _ = build_handler(
        [
            build_change_event(
                "chg_other_tenant",
                INCIDENT_TIME,
                tenant_id="tenant_002",
            )
        ]
    )

    with pytest.raises(ChangeSourceError, match="trust boundary"):
        await handler.execute(valid_payload(), "trc_001")


class FixedPermissionProvider:
    async def get_grant(
        self,
        tenant_id: str,
        operator_id: str,
    ) -> ToolPermissionGrant:
        return ToolPermissionGrant(
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission_tags=frozenset({"tenant:observe"}),
        )


async def test_registration_requires_specific_changes_read_permission() -> None:
    handler, _ = build_handler([])
    registry = ToolHandlerRegistry()
    definition = register_change_events_query_tool(registry, handler)

    assert definition.tool_name == "changes.query"
    assert definition.version == "v1"
    assert definition.risk_level is ToolRiskLevel.LOW
    assert definition.permission_tags == ("changes:read", "tenant:observe")
    registration = registry.get("changes.query", "v1")
    assert registration.definition is definition
    assert registration.handler is handler

    checker = ToolPermissionChecker(
        FixedPermissionProvider(),
        clock=lambda: INCIDENT_TIME,
    )
    with pytest.raises(PermissionDenied):
        await checker.check(definition, "tenant_001", "operator_001")


def test_controlled_workflow_maps_change_tool_to_change_evidence() -> None:
    assert (
        ControlledAgentWorkflow._evidence_type_for_tool(  # noqa: SLF001
            "changes.query"
        )
        is EvidenceType.CHANGE
    )
