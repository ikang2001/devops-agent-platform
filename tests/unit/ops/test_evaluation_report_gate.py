import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_ROOT = PROJECT_ROOT / "ops" / "product"


def load_module(filename: str, module_name: str):
    path = PRODUCT_ROOT / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_yaml(name: str):
    return yaml.safe_load((PRODUCT_ROOT / name).read_text(encoding="utf-8"))


def reports():
    runner = load_module(
        "run_evaluation.py",
        "evaluation_gate_source_runner",
    )
    dataset = read_yaml("evaluation-dataset.example.yml")
    baseline_responses = read_yaml("evaluation-responses.example.yml")
    candidate_responses = deepcopy(baseline_responses)
    candidate_responses["run"]["prompt_version"] = "rca-report-v2"
    return (
        runner.evaluate(dataset, baseline_responses),
        runner.evaluate(dataset, candidate_responses),
    )


def test_unchanged_quality_passes_for_human_release_review_only() -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "passing_evaluation_report_gate",
    )
    baseline, candidate = reports()

    decision = gate.compare_reports(
        baseline,
        candidate,
        read_yaml("evaluation-gate-policy.example.yml"),
    )

    assert decision["decision"] == "PASS"
    assert decision["next_action"] == "HUMAN_RELEASE_REVIEW"
    assert decision["automatic_rollout"] is False
    assert all(check["passed"] for check in decision["checks"])


def test_accuracy_regression_is_rejected() -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "accuracy_evaluation_report_gate",
    )
    runner = load_module(
        "run_evaluation.py",
        "accuracy_evaluation_gate_source_runner",
    )
    baseline, _ = reports()
    responses = read_yaml("evaluation-responses.example.yml")
    responses["run"]["prompt_version"] = "rca-report-v2"
    responses["responses"][0]["root_cause_category"] = "unknown"
    candidate = runner.evaluate(
        read_yaml("evaluation-dataset.example.yml"),
        responses,
    )

    decision = gate.compare_reports(
        baseline,
        candidate,
        read_yaml("evaluation-gate-policy.example.yml"),
    )

    assert decision["decision"] == "FAIL"
    assert decision["next_action"] == "REJECT_CANDIDATE"
    failed_metrics = {
        check.get("metric")
        for check in decision["checks"]
        if not check["passed"]
    }
    assert "root_cause_hit_rate" in failed_metrics
    assert "overall_pass_rate" in failed_metrics


def test_forbidden_claim_regression_is_rejected_by_safety_gate() -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "safety_evaluation_report_gate",
    )
    runner = load_module(
        "run_evaluation.py",
        "safety_evaluation_gate_source_runner",
    )
    baseline, _ = reports()
    responses = read_yaml("evaluation-responses.example.yml")
    responses["run"]["prompt_version"] = "rca-report-v2"
    responses["responses"][0]["report_text"] += (
        " Database corruption is also confirmed."
    )
    candidate = runner.evaluate(
        read_yaml("evaluation-dataset.example.yml"),
        responses,
    )

    decision = gate.compare_reports(
        baseline,
        candidate,
        read_yaml("evaluation-gate-policy.example.yml"),
    )

    assert decision["decision"] == "FAIL"
    safety_check = next(
        check
        for check in decision["checks"]
        if check["check"] == "required_forbidden_claim_free_rate"
    )
    assert safety_check["passed"] is False
    assert safety_check["required"] == 1.0


def test_missing_candidate_response_is_a_valid_failed_report() -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "missing_response_evaluation_report_gate",
    )
    runner = load_module(
        "run_evaluation.py",
        "missing_response_evaluation_gate_source_runner",
    )
    baseline, _ = reports()
    responses = read_yaml("evaluation-responses.example.yml")
    responses["run"]["prompt_version"] = "rca-report-v2"
    responses["responses"].pop()
    candidate = runner.evaluate(
        read_yaml("evaluation-dataset.example.yml"),
        responses,
    )

    decision = gate.compare_reports(
        baseline,
        candidate,
        read_yaml("evaluation-gate-policy.example.yml"),
    )

    assert decision["decision"] == "FAIL"
    assert decision["next_action"] == "REJECT_CANDIDATE"


def test_dataset_or_ordered_case_mismatch_fails_closed() -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "dataset_evaluation_report_gate",
    )
    baseline, candidate = reports()
    candidate["dataset_version"] = "v2"

    with pytest.raises(ValueError, match="same dataset and ordered cases"):
        gate.compare_reports(
            baseline,
            candidate,
            read_yaml("evaluation-gate-policy.example.yml"),
        )


def test_candidate_run_must_describe_a_real_variant() -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "run_metadata_evaluation_report_gate",
    )
    baseline, candidate = reports()
    candidate["run"] = deepcopy(baseline["run"])

    with pytest.raises(ValueError, match="must differ from baseline"):
        gate.compare_reports(
            baseline,
            candidate,
            read_yaml("evaluation-gate-policy.example.yml"),
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_or_tampered_report_rate_fails_closed(value: float) -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "invalid_rate_evaluation_report_gate",
    )
    baseline, candidate = reports()
    candidate["metrics"]["root_cause_hit_rate"] = value

    with pytest.raises(ValueError, match="finite rate"):
        gate.compare_reports(
            baseline,
            candidate,
            read_yaml("evaluation-gate-policy.example.yml"),
        )


def test_summary_metrics_are_recomputed_from_samples() -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "tampered_evaluation_report_gate",
    )
    baseline, candidate = reports()
    candidate["samples"][0]["root_cause_passed"] = False
    candidate["samples"][0]["passed"] = False

    with pytest.raises(ValueError, match="inconsistent with samples"):
        gate.compare_reports(
            baseline,
            candidate,
            read_yaml("evaluation-gate-policy.example.yml"),
        )


@pytest.mark.parametrize(
    ("detail_field", "detail_value", "message"),
    (
        (
            "missing_evidence",
            ["ev_missing"],
            "required_evidence_passed is inconsistent",
        ),
        (
            "forbidden_claims_found",
            ["unsupported claim"],
            "forbidden_claims_passed is inconsistent",
        ),
    ),
)
def test_sample_detail_lists_must_match_result_flags(
    detail_field: str,
    detail_value: list[str],
    message: str,
) -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "sample_details_evaluation_report_gate",
    )
    baseline, candidate = reports()
    candidate["samples"][0][detail_field] = detail_value

    with pytest.raises(ValueError, match=message):
        gate.compare_reports(
            baseline,
            candidate,
            read_yaml("evaluation-gate-policy.example.yml"),
        )


def test_policy_cannot_weaken_hard_safety_boundaries() -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "policy_evaluation_report_gate",
    )
    baseline, candidate = reports()
    policy = read_yaml("evaluation-gate-policy.example.yml")
    policy["minimum_overall_pass_rate"] = 0.9

    with pytest.raises(ValueError, match="cannot be lower than 0.95"):
        gate.compare_reports(baseline, candidate, policy)


def test_cli_creates_decision_once_and_refuses_overwrite(tmp_path: Path) -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "cli_evaluation_report_gate",
    )
    baseline, candidate = reports()
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    output = tmp_path / "decision.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    args = [
        "--baseline",
        str(baseline_path),
        "--candidate",
        str(candidate_path),
        "--policy",
        str(PRODUCT_ROOT / "evaluation-gate-policy.example.yml"),
        "--output",
        str(output),
    ]

    assert gate.main(args) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["decision"] == "PASS"
    assert gate.main(args) == 2


def test_cli_writes_fail_decision_and_returns_regression_status(
    tmp_path: Path,
) -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "failing_cli_evaluation_report_gate",
    )
    baseline, candidate = reports()
    candidate["samples"][0]["root_cause_passed"] = False
    candidate["samples"][0]["passed"] = False
    candidate["metrics"]["root_cause_hit_rate"] = 0.5
    candidate["metrics"]["overall_pass_rate"] = 0.5
    candidate["passed"] = False
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    output = tmp_path / "failed-decision.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    result = gate.main(
        [
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
            "--policy",
            str(PRODUCT_ROOT / "evaluation-gate-policy.example.yml"),
            "--output",
            str(output),
        ]
    )

    assert result == 1
    decision = json.loads(output.read_text(encoding="utf-8"))
    assert decision["decision"] == "FAIL"
    assert decision["next_action"] == "REJECT_CANDIDATE"
    assert decision["automatic_rollout"] is False


def test_cli_invalid_comparison_does_not_create_decision(tmp_path: Path) -> None:
    gate = load_module(
        "compare_evaluation_reports.py",
        "invalid_cli_evaluation_report_gate",
    )
    baseline, candidate = reports()
    candidate["dataset_version"] = "v2"
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    output = tmp_path / "invalid-decision.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    result = gate.main(
        [
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
            "--policy",
            str(PRODUCT_ROOT / "evaluation-gate-policy.example.yml"),
            "--output",
            str(output),
        ]
    )

    assert result == 2
    assert output.exists() is False
