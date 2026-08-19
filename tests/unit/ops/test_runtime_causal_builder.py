from devops_agent_platform.evaluation.causal_builder import (
    RuntimeCausalContextBuilder,
    RuntimeEvidenceFact,
)
from devops_agent_platform.evaluation.schemas import CausalEdge, RootCauseRef


def test_runtime_builder_replaces_free_form_chain_with_evidence_backed_edges() -> None:
    context = RuntimeCausalContextBuilder().build(
        root_cause=RootCauseRef(
            service="inventory-service",
            type="dependency_timeout",
            resource="postgres",
        ),
        conclusion_status="CANDIDATE",
        incident_service="inventory-service",
        entry_service="checkout-service",
        evidence=(
            RuntimeEvidenceFact(
                "ev-log",
                "Checkout records inventory-service as the failed downstream.",
            ),
            RuntimeEvidenceFact(
                "ev-trace",
                "The checkout trace contains an ERROR inventory.reserve span.",
            ),
        ),
        supporting_evidence_ids=("ev-log", "ev-trace"),
        proposed_chain=(
            CausalEdge(
                from_node="inventory-service",
                to_node="invented-service",
                evidence_ids=("ev-trace",),
            ),
            CausalEdge(
                from_node="checkout-service",
                to_node="inventory-service",
                evidence_ids=("ev-trace",),
            ),
        ),
        proposed_affected_services=("invented-service",),
    )

    assert tuple(
        (item.from_node, item.to_node) for item in context.causal_chain
    ) == (
        ("postgres", "service:inventory-service"),
        ("service:inventory-service", "service:checkout-service"),
    )
    assert context.affected_services == (
        "inventory-service",
        "checkout-service",
    )


def test_runtime_builder_clears_no_actionable_impact() -> None:
    context = RuntimeCausalContextBuilder().build(
        root_cause=None,
        conclusion_status="NO_ACTIONABLE_ROOT_CAUSE",
        incident_service="checkout-service",
        entry_service="checkout-service",
        evidence=(RuntimeEvidenceFact("ev-fp", "No customer impact."),),
        supporting_evidence_ids=("ev-fp",),
        proposed_chain=(
            CausalEdge(
                from_node="alert-rule",
                to_node="checkout-service",
                evidence_ids=("ev-fp",),
            ),
        ),
        proposed_affected_services=("checkout-service",),
    )

    assert context.causal_chain == ()
    assert context.affected_services == ()


def test_runtime_builder_extracts_downstream_edge_when_root_is_entry_service() -> None:
    context = RuntimeCausalContextBuilder().build(
        root_cause=RootCauseRef(
            service="checkout-service",
            type="cascading_failure",
            resource="checkout-service",
        ),
        conclusion_status="CANDIDATE",
        incident_service="checkout-service",
        entry_service="checkout-service",
        evidence=(
            RuntimeEvidenceFact("ev-metric", "Cascade fault is enabled."),
            RuntimeEvidenceFact(
                "ev-trace",
                "Trace contains the observed downstream failure edge "
                "checkout-service -> payment-service.",
            ),
        ),
        supporting_evidence_ids=("ev-metric", "ev-trace"),
        proposed_chain=(),
        proposed_affected_services=(),
    )

    assert tuple(
        (item.from_node, item.to_node, item.evidence_ids)
        for item in context.causal_chain
    ) == (("service:checkout-service", "service:payment-service", ("ev-trace",)),)
    assert context.affected_services == (
        "checkout-service",
        "payment-service",
    )
