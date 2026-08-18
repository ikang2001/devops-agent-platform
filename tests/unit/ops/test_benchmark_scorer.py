from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from devops_agent_platform.evaluation.schemas import RCAPrediction, ScenarioGroundTruth
from devops_agent_platform.evaluation.scorer import BenchmarkScorer, summarize_scores


def ground_truth_payload() -> dict[str, object]:
    return {
        "scenario_id": "inventory-db-timeout",
        "scenario_version": "1.0",
        "root_cause": {
            "service": "inventory-service",
            "type": "dependency_timeout",
            "resource": "postgres",
        },
        "required_evidence_types": ["METRIC", "LOG", "TRACE"],
        "optional_evidence_types": ["RUNBOOK"],
        "causal_chain": [
            {"from_node": "postgres", "to_node": "inventory-service"},
            {"from_node": "inventory-service", "to_node": "checkout-service"},
        ],
        "affected_services": ["inventory-service", "checkout-service"],
        "forbidden_claims": ["payment-service caused the checkout failure"],
        "expected_tool_types": [
            "metrics.query@v1",
            "logs.query@v1",
            "traces.query@v1",
        ],
        "forbidden_tool_types": ["remediation.execute@v1", "shell"],
    }


def prediction_payload() -> dict[str, object]:
    return {
        "scenario_id": "inventory-db-timeout",
        "run_id": "run-001",
        "root_cause": {
            "service": "inventory-service",
            "type": "dependency_timeout",
            "resource": "postgres",
        },
        "conclusion_status": "CANDIDATE",
        "confidence": 0.82,
        "evidence_ids": ["ev-metric", "ev-log", "ev-trace"],
        "evidence_types": ["METRIC", "LOG", "TRACE"],
        "claims": [
            {
                "claim_type": "ROOT_CAUSE",
                "statement": "inventory-service timed out while accessing postgres",
                "evidence_ids": ["ev-log", "ev-trace"],
            }
        ],
        "causal_chain": [
            {
                "from_node": "postgres",
                "to_node": "inventory-service",
                "evidence_ids": ["ev-log"],
            },
            {
                "from_node": "inventory-service",
                "to_node": "checkout-service",
                "evidence_ids": ["ev-trace"],
            },
        ],
        "affected_services": ["inventory-service", "checkout-service"],
        "tool_calls": [
            {
                "tool_type": "metrics.query@v1",
                "status": "SUCCEEDED",
                "evidence_ids": ["ev-metric"],
            },
            {
                "tool_type": "logs.query@v1",
                "status": "SUCCEEDED",
                "evidence_ids": ["ev-log"],
            },
            {
                "tool_type": "traces.query@v1",
                "status": "SUCCEEDED",
                "evidence_ids": ["ev-trace"],
            },
        ],
        "investigation_steps": 3,
        "llm_calls": 1,
        "latency_ms": 1200,
        "total_tokens": 400,
        "estimated_cost": 0.01,
    }


def score(
    ground_truth: dict[str, object] | None = None,
    prediction: dict[str, object] | None = None,
):
    expected = ScenarioGroundTruth.model_validate(
        ground_truth if ground_truth is not None else ground_truth_payload()
    )
    actual = RCAPrediction.model_validate(
        prediction if prediction is not None else prediction_payload()
    )
    return BenchmarkScorer().score(expected, actual)


def test_complete_prediction_receives_a_strict_pass() -> None:
    result = score()

    assert result.passed is True
    assert result.rca_exact_match is True
    assert result.strict_rca_match is True
    assert result.evidence_precision == 1.0
    assert result.evidence_recall == 1.0
    assert result.evidence_f1 == 1.0
    assert result.unsupported_claim_rate == 0.0
    assert result.causal_chain_f1 == 1.0
    assert result.blast_radius_f1 == 1.0
    assert result.tool_selection_accuracy == 1.0
    assert result.redundant_tool_call_rate == 0.0
    assert result.invalid_tool_proposal_rate == 0.0


def test_wrong_root_service_and_type_fail_rca_metrics() -> None:
    actual = prediction_payload()
    actual["root_cause"] = {
        "service": "payment-service",
        "type": "application_error",
        "resource": None,
    }

    result = score(prediction=actual)

    assert result.passed is False
    assert result.root_service_correct is False
    assert result.root_type_correct is False
    assert result.rca_exact_match is False


def test_partial_evidence_calculates_precision_recall_and_f1() -> None:
    actual = prediction_payload()
    actual["evidence_types"] = ["METRIC", "TRACE", "KNOWLEDGE"]

    result = score(prediction=actual)

    assert result.evidence_precision == pytest.approx(2 / 3)
    assert result.evidence_recall == pytest.approx(2 / 3)
    assert result.evidence_f1 == pytest.approx(2 / 3)
    assert result.passed is False


def test_prediction_schema_rejects_duplicate_evidence() -> None:
    actual = prediction_payload()
    actual["evidence_ids"] = ["ev-log", "ev-log"]

    with pytest.raises(ValidationError, match="unique"):
        RCAPrediction.model_validate(actual)


def test_empty_prediction_is_scored_without_division_errors() -> None:
    actual = {
        "scenario_id": "inventory-db-timeout",
        "run_id": "run-empty",
        "root_cause": None,
        "conclusion_status": "UNDETERMINED",
        "confidence": 0.0,
        "evidence_ids": [],
        "evidence_types": [],
        "claims": [],
        "causal_chain": [],
        "affected_services": [],
        "tool_calls": [],
        "investigation_steps": 0,
        "llm_calls": 0,
        "latency_ms": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
    }

    result = score(prediction=actual)

    assert result.passed is False
    assert result.evidence_precision == 0.0
    assert result.evidence_recall == 0.0
    assert result.causal_chain_f1 == 0.0
    assert result.blast_radius_f1 == 0.0


def test_no_root_cause_scenario_rewards_an_undetermined_result() -> None:
    expected = ground_truth_payload()
    expected["root_cause"] = None
    expected["required_evidence_types"] = ["METRIC"]
    expected["causal_chain"] = []
    expected["affected_services"] = []
    expected["expected_tool_types"] = ["metrics.query@v1"]
    actual = prediction_payload()
    actual.update(
        {
            "root_cause": None,
            "conclusion_status": "NO_ACTIONABLE_ROOT_CAUSE",
            "evidence_ids": ["ev-metric"],
            "evidence_types": ["METRIC"],
            "claims": [],
            "causal_chain": [],
            "affected_services": [],
            "tool_calls": [
                {
                    "tool_type": "metrics.query@v1",
                    "status": "SUCCEEDED",
                    "evidence_ids": ["ev-metric"],
                }
            ],
        }
    )

    result = score(ground_truth=expected, prediction=actual)

    assert result.false_positive_root_cause is False
    assert result.rca_exact_match is True
    assert result.passed is True


def test_no_root_cause_scenario_detects_a_false_positive() -> None:
    expected = ground_truth_payload()
    expected["root_cause"] = None
    expected["causal_chain"] = []
    expected["affected_services"] = []

    result = score(ground_truth=expected)

    assert result.false_positive_root_cause is True
    assert result.passed is False


def test_causal_chain_direction_is_part_of_the_score() -> None:
    actual = prediction_payload()
    chain = deepcopy(actual["causal_chain"])
    assert isinstance(chain, list)
    chain[0]["from_node"] = "inventory-service"
    chain[0]["to_node"] = "postgres"
    actual["causal_chain"] = chain

    result = score(prediction=actual)

    assert result.causal_chain_precision == 0.5
    assert result.causal_chain_recall == 0.5
    assert result.causal_chain_f1 == 0.5
    assert result.passed is False


def test_blast_radius_penalizes_an_unrelated_service() -> None:
    actual = prediction_payload()
    actual["affected_services"] = [
        "inventory-service",
        "checkout-service",
        "notification-service",
    ]

    result = score(prediction=actual)

    assert result.blast_radius_precision == pytest.approx(2 / 3)
    assert result.blast_radius_recall == 1.0
    assert result.passed is False


def test_forbidden_and_unsupported_claims_are_reported() -> None:
    actual = prediction_payload()
    claims = deepcopy(actual["claims"])
    assert isinstance(claims, list)
    claims.extend(
        [
            {
                "claim_type": "DEPENDENCY_FAILURE",
                "statement": "payment-service caused the checkout failure",
                "evidence_ids": ["ev-unknown"],
            },
            {
                "claim_type": "CHANGE",
                "statement": "a recent configuration change is suspected",
                "evidence_ids": [],
            },
        ]
    )
    actual["claims"] = claims

    result = score(prediction=actual)

    assert result.forbidden_claims_found == (
        "payment-service caused the checkout failure",
    )
    assert result.unsupported_claim_count == 2
    assert result.unsupported_claim_rate == pytest.approx(2 / 5)
    assert result.passed is False


def test_tool_metrics_detect_redundant_and_invalid_proposals() -> None:
    actual = prediction_payload()
    tool_calls = deepcopy(actual["tool_calls"])
    assert isinstance(tool_calls, list)
    tool_calls.extend(
        [
            {
                "tool_type": "logs.query@v1",
                "status": "SUCCEEDED",
                "evidence_ids": [],
            },
            {
                "tool_type": "shell",
                "status": "BLOCKED",
                "evidence_ids": [],
                "proposal_valid": False,
            },
        ]
    )
    actual["tool_calls"] = tool_calls

    result = score(prediction=actual)

    assert result.redundant_tool_call_rate == pytest.approx(1 / 5)
    assert result.invalid_tool_proposal_rate == pytest.approx(1 / 5)
    assert result.policy_block_rate == pytest.approx(1 / 5)
    assert result.forbidden_tool_proposed is True
    assert result.passed is False


def test_summary_aggregates_real_scores() -> None:
    passing = score()
    failing_payload = prediction_payload()
    failing_payload["root_cause"] = {
        "service": "payment-service",
        "type": "application_error",
        "resource": None,
    }
    failing = score(prediction=failing_payload)

    summary = summarize_scores((passing, failing))

    assert summary.total_runs == 2
    assert summary.passed_runs == 1
    assert summary.rca_top1_accuracy == 0.5
    assert summary.root_service_accuracy == 0.5
    assert summary.evidence_recall == 1.0
    assert summary.false_positive_rate == 0.0
    assert summary.avg_investigation_steps == 3.0
    assert summary.redundant_tool_call_rate == 0.0
    assert summary.p50_latency_ms == 1200
    assert summary.p95_latency_ms == 1200


def test_prediction_requires_a_supported_root_cause_claim() -> None:
    actual = prediction_payload()
    actual["claims"] = []

    with pytest.raises(ValidationError, match="ROOT_CAUSE"):
        RCAPrediction.model_validate(actual)
