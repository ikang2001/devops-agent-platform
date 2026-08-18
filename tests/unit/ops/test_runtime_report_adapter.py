from dataclasses import replace
from datetime import UTC, datetime

import pytest

from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
    ToolInvocationStatus,
    ToolRiskLevel,
)
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.tool_invocation import (
    ToolInvocation,
    build_tool_payload_sha256,
)
from devops_agent_platform.evaluation.runtime_adapter import (
    RCAReportPredictionAdapter,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def build_evidence_item(
    evidence_id: str,
    evidence_type: EvidenceType,
    step_id: str,
    tool_name: str,
) -> Evidence:
    content_json, content_sha256 = build_evidence_content(
        {"message": "redacted observation", "source": evidence_type.value}
    )
    return Evidence(
        evidence_id=evidence_id,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id=step_id,
        tool_name=tool_name,
        tool_version="v1",
        evidence_type=evidence_type,
        source="reference",
        summary="safe evidence summary",
        content_json=content_json,
        content_sha256=content_sha256,
        confidence=1.0,
        collected_at=NOW,
    )


def build_invocation(
    invocation_id: str,
    step_id: str,
    tool_name: str,
    status: ToolInvocationStatus = ToolInvocationStatus.SUCCEEDED,
) -> ToolInvocation:
    input_sha256 = build_tool_payload_sha256({"query": "redacted"})
    output_sha256 = (
        build_tool_payload_sha256({"result": "redacted"})
        if status is ToolInvocationStatus.SUCCEEDED
        else None
    )
    return ToolInvocation(
        invocation_id=invocation_id,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id=step_id,
        operator_id="operator_001",
        trace_id="trace_001",
        tool_name=tool_name,
        tool_version="v1",
        risk_level=ToolRiskLevel.LOW,
        status=status,
        input_summary="redacted query",
        input_sha256=input_sha256,
        output_summary="redacted output" if output_sha256 else None,
        output_sha256=output_sha256,
        latency_ms=25,
        error_code=None if output_sha256 else "DEPENDENCY_TIMEOUT",
        started_at=NOW,
        ended_at=NOW,
    )


def build_report() -> RCAReport:
    return RCAReport(
        report_id="r" * 64,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.UNDETERMINED,
        title="Review required",
        summary="Sensitive raw output must not be copied into the benchmark.",
        confidence=0.0,
        evidence_ids=("e" * 64, "f" * 64),
        evidence_type_counts=(("LOG", 1), ("METRIC", 1)),
        recommendations=("Review evidence.",),
        generator_name="deterministic",
        generator_version="v1",
        generated_at=NOW,
        causal_chain=(("service:payment", "service:checkout", ("f" * 64,)),),
        affected_services=("payment-service", "checkout-service"),
    )


def runtime_records() -> tuple[tuple[Evidence, ...], tuple[ToolInvocation, ...]]:
    evidence = (
        build_evidence_item(
            "e" * 64, EvidenceType.METRIC, "step.metric", "metrics.query"
        ),
        build_evidence_item(
            "f" * 64, EvidenceType.DEPLOYMENT, "step.change", "changes.query"
        ),
    )
    invocations = (
        build_invocation("i" * 64, "step.metric", "metrics.query"),
        build_invocation("j" * 64, "step.change", "changes.query"),
    )
    return evidence, invocations


def test_adapter_maps_runtime_report_without_ground_truth_or_raw_output() -> None:
    evidence, invocations = runtime_records()
    report = replace(
        build_report(),
        conclusion_status=RCAConclusionStatus.CANDIDATE,
        confidence=0.8,
        suspected_root_node="service:payment",
    )

    prediction = RCAReportPredictionAdapter().adapt(
        report,
        evidence,
        invocations,
        scenario_id="deployment-regression",
        root_cause_type="deployment_regression",
        root_cause_service="payment-service",
        llm_calls=1,
        total_tokens=120,
        estimated_cost=0.004,
    )

    assert prediction.scenario_id == "deployment-regression"
    assert prediction.evidence_types == (EvidenceType.CHANGE, EvidenceType.METRIC)
    assert prediction.causal_chain[0].from_node == "service:payment"
    assert prediction.affected_services == (
        "payment-service",
        "checkout-service",
    )
    assert [item.tool_type for item in prediction.tool_calls] == [
        "metrics.query@v1",
        "changes.query@v1",
    ]
    assert prediction.latency_ms == 50
    assert "Sensitive raw output" in prediction.claims[0].statement
    assert "redacted output" not in prediction.model_dump_json()


def test_adapter_downgrades_typed_root_claim_when_report_has_no_root_type() -> None:
    evidence, invocations = runtime_records()
    report = replace(
        build_report(),
        conclusion_status=RCAConclusionStatus.CANDIDATE,
        confidence=0.9,
        suspected_root_node="service:payment",
    )

    prediction = RCAReportPredictionAdapter().adapt(
        report,
        evidence,
        invocations,
        scenario_id="deployment-regression",
    )

    assert prediction.root_cause is None
    assert prediction.conclusion_status.value == "UNDETERMINED"
    assert prediction.confidence == 0
    assert prediction.claims == ()


def test_adapter_rejects_missing_or_cross_execution_records() -> None:
    evidence, invocations = runtime_records()
    adapter = RCAReportPredictionAdapter()

    with pytest.raises(ValueError, match="exactly match"):
        adapter.adapt(
            build_report(),
            evidence[:1],
            invocations,
            scenario_id="deployment-regression",
        )

    mismatched = replace(evidence[0], execution_attempt=2)
    with pytest.raises(ValueError, match="does not match"):
        adapter.adapt(
            build_report(),
            (mismatched, evidence[1]),
            invocations,
            scenario_id="deployment-regression",
        )


def test_adapter_rejects_invalid_external_measurements() -> None:
    evidence, invocations = runtime_records()

    with pytest.raises(ValueError, match="finite"):
        RCAReportPredictionAdapter().adapt(
            build_report(),
            evidence,
            invocations,
            scenario_id="deployment-regression",
            estimated_cost=float("nan"),
        )
