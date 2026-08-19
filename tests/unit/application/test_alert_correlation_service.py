from datetime import UTC, datetime, timedelta

from devops_agent_platform.application.services.alert_correlation_service import (
    AlertCorrelationService,
)
from devops_agent_platform.domain.enums import AlertSeverity, IncidentStatus
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.topology import (
    DependencyEdge,
    ServiceNode,
    TopologyGraph,
    TopologySource,
)

NOW = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


def _alert(
    service_name: str,
    *,
    tenant_id: str = "tenant-1",
    environment: str = "prod",
    starts_at: datetime = NOW,
) -> Alert:
    return Alert(
        alert_id=f"alert-{service_name}",
        tenant_id=tenant_id,
        source="alertmanager",
        service_name=service_name,
        severity=AlertSeverity.CRITICAL,
        summary="request timeout",
        starts_at=starts_at,
        fingerprint="timeout",
        external_event_id=f"event-{service_name}",
        environment=environment,
    )


def _incident(
    service_name: str,
    *,
    tenant_id: str = "tenant-1",
    environment: str = "prod",
    created_at: datetime = NOW - timedelta(minutes=5),
) -> Incident:
    return Incident(
        incident_id=f"incident-{service_name}",
        tenant_id=tenant_id,
        service_name=service_name,
        severity=AlertSeverity.WARNING,
        status=IncidentStatus.OPEN,
        title="active incident",
        created_at=created_at,
        updated_at=created_at,
        environment=environment,
    )


def _graph(*services: str) -> TopologyGraph:
    nodes = tuple(
        ServiceNode(
            node_id=f"service:{service}",
            tenant_id="tenant-1",
            service_name=service,
            environment="prod",
            source=TopologySource.STATIC,
        )
        for service in services
    )
    edges = tuple(
        DependencyEdge(
            edge_id=f"edge-{index}",
            tenant_id="tenant-1",
            source_node_id=f"service:{left}",
            target_node_id=f"service:{right}",
        )
        for index, (left, right) in enumerate(
            zip(services[:-1], services[1:], strict=True),
            start=1,
        )
    )
    return TopologyGraph("tenant-1", nodes, edges)


def test_correlation_accepts_transitive_topology_within_same_environment() -> None:
    decision = AlertCorrelationService().correlate(
        _alert("checkout-api"),
        [_incident("postgres")],
        topology=_graph("postgres", "inventory-api", "checkout-api"),
    )
    assert decision.incident_id == "incident-postgres"
    assert decision.root_service == "postgres"
    assert decision.affected_services == ("postgres", "checkout-api")


def test_correlation_rejects_tenant_environment_time_and_unrelated_service() -> None:
    service = AlertCorrelationService()
    assert service.correlate(
        _alert("checkout-api", tenant_id="tenant-2"),
        [_incident("checkout-api")],
    ).incident_id is None
    assert service.correlate(
        _alert("checkout-api", environment="staging"),
        [_incident("checkout-api")],
    ).incident_id is None
    assert service.correlate(
        _alert("checkout-api", starts_at=NOW + timedelta(hours=1)),
        [_incident("checkout-api")],
    ).incident_id is None
    assert service.correlate(
        _alert("checkout-api"),
        [_incident("payments-api")],
        topology=_graph("payments-api", "inventory-api", "checkout-api"),
    ).incident_id == "incident-payments-api"
    assert service.correlate(
        _alert("checkout-api"),
        [_incident("payments-api")],
        topology=_graph("payments-api", "notifications-api"),
    ).incident_id is None
