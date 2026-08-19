from __future__ import annotations

from datetime import UTC, datetime

from devops_agent_platform.causal_context import (
    TopologyCausalContextBuilder,
    normalize_affected_service,
    normalize_causal_node,
)
from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
)
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.topology import (
    DependencyEdge,
    ServiceNode,
    TopologyGraph,
    TopologySource,
)


def _evidence(
    evidence_id: str,
    evidence_type: EvidenceType,
    tool_name: str,
    summary: str,
) -> Evidence:
    content, digest = build_evidence_content({"summary": summary})
    return Evidence(
        evidence_id=evidence_id,
        tenant_id="tenant-1",
        incident_id="incident-1",
        workflow_run_id="workflow-1",
        execution_attempt=1,
        step_id=f"step-{evidence_id}",
        tool_name=tool_name,
        tool_version="v1",
        evidence_type=evidence_type,
        source="test",
        summary=summary,
        content_json=content,
        content_sha256=digest,
        confidence=1.0,
        collected_at=datetime.now(UTC),
    )


def _report(
    evidence: tuple[Evidence, ...],
    *,
    status: RCAConclusionStatus = RCAConclusionStatus.UNDETERMINED,
    chain: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    affected: tuple[str, ...] = (),
) -> RCAReport:
    counts: dict[str, int] = {}
    for item in evidence:
        counts[item.evidence_type.value] = counts.get(item.evidence_type.value, 0) + 1
    return RCAReport(
        report_id="report-1",
        tenant_id="tenant-1",
        incident_id="incident-1",
        workflow_run_id="workflow-1",
        execution_attempt=1,
        conclusion_status=status,
        title="Test RCA report",
        summary="Evidence-backed deterministic causal context.",
        confidence=0.4,
        evidence_ids=tuple(item.evidence_id for item in evidence),
        evidence_type_counts=tuple(sorted(counts.items())),
        recommendations=(),
        generator_name="test",
        generator_version="v1",
        generated_at=datetime.now(UTC),
        suspected_root_node="checkout",
        causal_chain=chain,
        affected_services=affected,
        blast_radius=(("service:unrelated-service", 1.0),) if affected else (),
    )


def _graph() -> TopologyGraph:
    services = ("checkout-service", "inventory-service", "payment-service")
    nodes = tuple(
        ServiceNode(
            node_id=f"service:{service}",
            tenant_id="tenant-1",
            service_name=service,
            environment="test",
            source=TopologySource.STATIC,
        )
        for service in services
    )
    edges = (
        DependencyEdge(
            edge_id="checkout-inventory",
            tenant_id="tenant-1",
            source_node_id="service:checkout-service",
            target_node_id="service:inventory-service",
        ),
        DependencyEdge(
            edge_id="checkout-payment",
            tenant_id="tenant-1",
            source_node_id="service:checkout-service",
            target_node_id="service:payment-service",
        ),
    )
    return TopologyGraph("tenant-1", nodes, edges)


def test_node_aliases_are_normalized_for_causal_scoring() -> None:
    assert normalize_causal_node("checkout") == "service:checkout-service"
    assert normalize_causal_node("checkout-service") == "service:checkout-service"
    assert normalize_causal_node("service:checkout-service") == (
        "service:checkout-service"
    )
    assert normalize_affected_service("service:checkout-service") == (
        "checkout-service"
    )


def test_builder_keeps_only_forward_observed_topology_edges() -> None:
    topology = _evidence(
        "ev-topology",
        EvidenceType.TOPOLOGY,
        "topology.query",
        "Topology snapshot collected.",
    )
    trace = _evidence(
        "ev-trace",
        EvidenceType.TRACE,
        "traces.query",
        "checkout-service failure propagates to inventory-service.",
    )
    report = _report(
        (topology, trace),
        chain=(
            ("checkout", "inventory-service", ("ev-trace",)),
            ("inventory-service", "checkout", ("ev-trace",)),
        ),
    )

    context = TopologyCausalContextBuilder().build(
        report=report,
        incident_service="checkout-service",
        topology=_graph(),
        evidence=(topology, trace),
    )

    assert context.root_node == "service:checkout-service"
    assert context.causal_chain == (
        (
            "service:checkout-service",
            "service:inventory-service",
            ("ev-trace",),
        ),
    )
    assert context.affected_services == ("inventory-service",)
    assert context.blast_radius == (("inventory-service", 1.0),)


def test_no_actionable_root_cause_has_no_causal_impact() -> None:
    topology = _evidence(
        "ev-topology",
        EvidenceType.TOPOLOGY,
        "topology.query",
        "Topology snapshot collected.",
    )
    report = _report(
        (topology,),
        status=RCAConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE,
        chain=(("checkout", "payment-service", ("ev-topology",)),),
        affected=("payment-service",),
    )

    context = TopologyCausalContextBuilder().build(
        report=report,
        incident_service="checkout-service",
        topology=_graph(),
        evidence=(topology,),
    )

    assert context == context.__class__(None, (), (), ())
