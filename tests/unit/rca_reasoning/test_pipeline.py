from __future__ import annotations

import json
from pathlib import Path

from devops_agent_platform.rca_reasoning import (
    ReasoningDecisionStatus,
    ReasoningEvidence,
    ReasoningEvidenceType,
    RootCauseReasoningPipeline,
    RootCauseTaxonomyMapper,
    RootCauseType,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_ROOT = PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios"
RUNTIME_INPUT = (
    PROJECT_ROOT
    / "ops"
    / "reference-staging"
    / "artifacts"
    / "reference-20260818-r2"
    / "minishop-v2-runtime-inputs.json"
)


def test_taxonomy_maps_aliases_and_refuses_unknown_types() -> None:
    mapper = RootCauseTaxonomyMapper()

    assert mapper.map_type("database timeout") is RootCauseType.DEPENDENCY_TIMEOUT
    assert mapper.map_type("redis_latency") is RootCauseType.DEPENDENCY_LATENCY
    assert mapper.map_type("made-up-cause") is RootCauseType.UNKNOWN


def test_all_twelve_ground_truth_candidates_are_ranked_first() -> None:
    suite = json.loads(RUNTIME_INPUT.read_text(encoding="utf-8"))
    manifests = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in SCENARIO_ROOT.glob("*.json")
    }
    pipeline = RootCauseReasoningPipeline()

    for case in suite["cases"]:
        expected = manifests[case["scenario_id"]]["ground_truth"]["root_cause"]
        result = pipeline.reason(
            incident_service=case["service_name"],
            incident_summary=case["summary"],
            evidence=tuple(
                ReasoningEvidence(
                    evidence_id=item["evidence_id"],
                    evidence_type=ReasoningEvidenceType.from_value(
                        item["evidence_type"]
                    ),
                    source=item["source"],
                    summary=item["summary"],
                )
                for item in case["evidence"]
            ),
        )

        assert result.candidates, case["scenario_id"]
        top = result.candidates[0].identity
        if expected is None:
            assert top.root_type is RootCauseType.NO_ACTIONABLE_ROOT_CAUSE
            assert (
                result.recommended_status
                is ReasoningDecisionStatus.NO_ACTIONABLE_ROOT_CAUSE
            )
        else:
            assert top.service == expected["service_name"], case["scenario_id"]
            assert (
                top.root_type.value == expected["root_cause_type"]
            ), case["scenario_id"]
            assert top.resource == expected["root_cause_resource"], case["scenario_id"]


def test_recent_change_without_signal_shift_remains_undetermined() -> None:
    result = RootCauseReasoningPipeline().reason(
        incident_service="payment-service",
        incident_summary="payment deployment investigation",
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-change",
                evidence_type=ReasoningEvidenceType.CHANGE,
                source="change",
                summary="payment-service v1 to v2 deployment completed successfully",
            ),
        ),
    )

    assert result.candidates[0].identity.root_type is RootCauseType.APPLICATION_ERROR
    assert result.recommended_status is ReasoningDecisionStatus.UNDETERMINED


def test_change_plus_post_change_error_produces_deployment_candidate() -> None:
    result = RootCauseReasoningPipeline().reason(
        incident_service="payment-service",
        incident_summary="payment errors increased after the v2 deployment",
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-change",
                evidence_type=ReasoningEvidenceType.CHANGE,
                source="change",
                summary="payment-service v1 to v2 deployment completed",
            ),
            ReasoningEvidence(
                evidence_id="ev-error",
                evidence_type=ReasoningEvidenceType.LOG,
                source="loki",
                summary="deployment_regression started after the deployment",
            ),
        ),
    )

    top = result.candidates[0]
    assert top.identity.root_type is RootCauseType.DEPLOYMENT_REGRESSION
    assert top.identity.resource == "payment-service:v2"
    assert top.missing_evidence == ()
    assert result.recommended_status is ReasoningDecisionStatus.CANDIDATE


def test_historical_knowledge_alone_cannot_create_a_confident_root_cause() -> None:
    result = RootCauseReasoningPipeline().reason(
        incident_service="payment-service",
        incident_summary="payment alert requires investigation",
        evidence=(
            ReasoningEvidence(
                evidence_id="ev-history",
                evidence_type=ReasoningEvidenceType.KNOWLEDGE,
                source="knowledge",
                summary=(
                    "Previous incident reported a provider timeout; historical only"
                ),
            ),
        ),
    )

    assert result.candidates[0].final_score < 0.5
    assert result.recommended_status is ReasoningDecisionStatus.UNDETERMINED
