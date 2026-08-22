from __future__ import annotations

from devops_agent_platform.rca_reasoning import (
    ReasoningEvidence,
    ReasoningEvidenceType,
    RootCauseReasoningPipeline,
    RootCauseType,
)


def test_strict_entity_resolution_uses_structured_otel_attributes() -> None:
    result = RootCauseReasoningPipeline(strict_entity_resolution=True).reason(
        incident_service="checkout-gateway",
        incident_summary="checkout request failed",
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-structured",
                evidence_type=ReasoningEvidenceType.LOG,
                source="loki",
                summary=(
                    "service.name=cart-api db.system=mysql "
                    "server.address=mysql-primary error.type=deadline_exceeded"
                ),
            ),
        ),
    )

    candidate = result.candidates[0]
    assert candidate.identity.service == "cart-api"
    assert candidate.identity.root_type is RootCauseType.DEPENDENCY_TIMEOUT
    assert candidate.identity.resource == "mysql-primary"


def test_strict_entity_resolution_does_not_guess_postgres_from_generic_timeout() -> (
    None
):
    result = RootCauseReasoningPipeline(strict_entity_resolution=True).reason(
        incident_service="checkout-service",
        incident_summary="database timeout detected",
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-generic",
                evidence_type=ReasoningEvidenceType.LOG,
                source="loki",
                summary="database operation exceeded deadline",
            ),
        ),
    )

    assert result.candidates[0].identity.resource is None


def test_json_log_entities_propagate_to_generic_signal_summaries() -> None:
    result = RootCauseReasoningPipeline(strict_entity_resolution=True).reason(
        incident_service="unknown-service",
        incident_summary=(
            'service_name="pricing-api" fault_type="dns_failure" '
            "request to pricing.internal failed after dependency timeout"
        ),
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-metric",
                evidence_type=ReasoningEvidenceType.METRIC,
                source="prometheus",
                summary="metrics collected; service.name=pricing-api",
            ),
            ReasoningEvidence(
                evidence_id="ev-log",
                evidence_type=ReasoningEvidenceType.LOG,
                source="loki",
                summary=(
                    'service_name="pricing-api" fault_type="dns_failure" '
                    "request to pricing.internal failed after dependency timeout"
                ),
            ),
        ),
    )

    candidate = result.candidates[0]
    assert candidate.identity.service == "pricing-api"
    assert candidate.identity.root_type is RootCauseType.DEPENDENCY_TIMEOUT
    assert candidate.identity.resource == "pricing.internal"
    assert set(candidate.supporting_evidence_ids) == {"ev-log", "ev-metric"}


def test_structured_error_context_overrides_correlated_alert_title() -> None:
    result = RootCauseReasoningPipeline(strict_entity_resolution=True).reason(
        incident_service="checkout-service",
        incident_summary="checkout-service p95 latency is higher than 1s",
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-metric",
                evidence_type=ReasoningEvidenceType.METRIC,
                source="prometheus",
                summary="metrics collected; service.name=checkout-service",
            ),
            ReasoningEvidence(
                evidence_id="ev-log",
                evidence_type=ReasoningEvidenceType.LOG,
                source="loki",
                summary=(
                    'service_name="checkout-service" '
                    'error.type="cascading_failure" '
                    'resource.name="checkout-service"'
                ),
            ),
            ReasoningEvidence(
                evidence_id="ev-trace",
                evidence_type=ReasoningEvidenceType.TRACE,
                source="tempo",
                summary="traces collected; service.name=checkout-service",
            ),
        ),
    )

    candidate = result.candidates[0]
    assert candidate.identity.service == "checkout-service"
    assert candidate.identity.root_type is RootCauseType.CASCADING_FAILURE
    assert candidate.identity.resource == "checkout-service"
    assert set(candidate.supporting_evidence_ids) == {
        "ev-log",
        "ev-metric",
        "ev-trace",
    }


def test_json_log_extraction_prefers_newest_meaningful_attributes() -> None:
    result = RootCauseReasoningPipeline(strict_entity_resolution=True).reason(
        incident_service="inventory-service",
        incident_summary="inventory dependency alert",
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-log",
                evidence_type=ReasoningEvidenceType.LOG,
                source="loki",
                summary=(
                    'log={"service_name":"inventory-service",'
                    '"error.type":null,"resource.name":null}; '
                    'log={"service_name":"inventory-service",'
                    '"error.type":"redis_latency","resource.name":"redis"}; '
                    'log={"service_name":"inventory-service",'
                    '"error.type":"db_timeout","resource.name":"postgres"}'
                ),
            ),
        ),
    )

    candidate = result.candidates[0]
    assert candidate.identity.root_type is RootCauseType.DEPENDENCY_LATENCY
    assert candidate.identity.resource == "redis"


def test_fault_type_and_resource_aliases_propagate_across_evidence() -> None:
    result = RootCauseReasoningPipeline(strict_entity_resolution=True).reason(
        incident_service="receipt-generator-api",
        incident_summary="operational anomaly requires investigation",
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-metric",
                evidence_type=ReasoningEvidenceType.METRIC,
                source="prometheus",
                summary="metrics collected; service.name=receipt-generator-api",
            ),
            ReasoningEvidence(
                evidence_id="ev-log",
                evidence_type=ReasoningEvidenceType.LOG,
                source="loki",
                summary=(
                    'service_name="receipt-generator-api" '
                    'fault_type="storage_throttling" '
                    'resource="blob-receipts-container"'
                ),
            ),
        ),
    )

    candidate = result.candidates[0]
    assert candidate.identity.root_type is RootCauseType.RESOURCE_EXHAUSTION
    assert candidate.identity.resource == "blob-receipts-container"
    assert set(candidate.supporting_evidence_ids) == {"ev-log", "ev-metric"}


def test_generic_dependency_words_and_emitting_service_are_not_resources() -> None:
    result = RootCauseReasoningPipeline(strict_entity_resolution=True).reason(
        incident_service="checkout-service",
        incident_summary="Checkout failed after the payment dependency call.",
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-log",
                evidence_type=ReasoningEvidenceType.LOG,
                source="loki",
                summary="payment-service emitted PAYMENT_GATEWAY_ERROR.",
            ),
        ),
    )

    candidate = result.candidates[0]
    assert candidate.identity.service == "payment-service"
    assert candidate.identity.root_type is RootCauseType.APPLICATION_ERROR
    assert candidate.identity.resource is None
