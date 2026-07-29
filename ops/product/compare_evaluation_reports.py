from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

_RUN_FIELDS = ("model_version", "prompt_version", "retrieval_version")
_RATE_METRICS = (
    "root_cause_hit_rate",
    "required_evidence_pass_rate",
    "forbidden_claim_free_rate",
    "ticket_priority_match_rate",
    "overall_pass_rate",
)
_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "dataset_id",
        "dataset_version",
        "generated_at",
        "run",
        "passed",
        "metrics",
        "samples",
    }
)
_SAMPLE_FIELDS = frozenset(
    {
        "case_id",
        "passed",
        "root_cause_passed",
        "required_evidence_passed",
        "forbidden_claims_passed",
        "ticket_priority_passed",
        "missing_evidence",
        "forbidden_claims_found",
    }
)
_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "minimum_overall_pass_rate",
        "required_forbidden_claim_free_rate",
        "require_no_metric_regression",
    }
)


@dataclass(frozen=True)
class ValidatedReport:
    dataset_id: str
    dataset_version: str
    run: dict[str, str]
    sample_count: int
    case_ids: tuple[str, ...]
    metrics: dict[str, float]


def compare_reports(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """比较同一数据集上的两个离线评测报告并给出失败关闭决策。"""
    baseline_report = _validate_report(baseline, "baseline")
    candidate_report = _validate_report(candidate, "candidate")
    validated_policy = _validate_policy(policy)
    _validate_comparison(baseline_report, candidate_report)

    checks = _build_checks(
        baseline_report.metrics,
        candidate_report.metrics,
        validated_policy,
    )
    passed = all(check["passed"] for check in checks)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "decision": "PASS" if passed else "FAIL",
        "next_action": (
            "HUMAN_RELEASE_REVIEW" if passed else "REJECT_CANDIDATE"
        ),
        "automatic_rollout": False,
        "dataset": {
            "dataset_id": baseline_report.dataset_id,
            "dataset_version": baseline_report.dataset_version,
            "sample_count": baseline_report.sample_count,
            "case_ids": list(baseline_report.case_ids),
        },
        "baseline_run": baseline_report.run,
        "candidate_run": candidate_report.run,
        "policy": validated_policy,
        "metrics": {
            "baseline": baseline_report.metrics,
            "candidate": candidate_report.metrics,
        },
        "checks": checks,
    }


def _validate_report(value: Any, label: str) -> ValidatedReport:
    if not isinstance(value, dict) or set(value) != _REPORT_FIELDS:
        raise ValueError(f"{label} report fields are invalid")
    if value.get("schema_version") != "1.0":
        raise ValueError(f"{label} report schema_version must be 1.0")
    dataset_id = _stable_text(value.get("dataset_id"), f"{label} dataset_id")
    dataset_version = _stable_text(
        value.get("dataset_version"),
        f"{label} dataset_version",
    )
    _text(value.get("generated_at"), f"{label} generated_at")
    run = _validate_run(value.get("run"), label)
    if not isinstance(value.get("passed"), bool):
        raise ValueError(f"{label} passed must be a boolean")

    samples = value.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError(f"{label} samples must be a non-empty list")
    case_ids = tuple(_validate_sample(item, label) for item in samples)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"{label} samples contain duplicate case_id")
    metrics = _validate_metrics(value.get("metrics"), len(samples), label)
    _validate_derived_results(value, metrics, label)
    return ValidatedReport(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        run=run,
        sample_count=len(samples),
        case_ids=case_ids,
        metrics=metrics,
    )


def _validate_sample(value: Any, label: str) -> str:
    if not isinstance(value, dict) or set(value) != _SAMPLE_FIELDS:
        raise ValueError(f"{label} sample fields are invalid")
    case_id = _stable_text(value.get("case_id"), f"{label} sample case_id")
    result_fields = (
        "passed",
        "root_cause_passed",
        "required_evidence_passed",
        "forbidden_claims_passed",
        "ticket_priority_passed",
    )
    if not all(isinstance(value.get(name), bool) for name in result_fields):
        raise ValueError(f"{label} sample result fields must be booleans")
    missing_evidence = _unique_strings(
        value.get("missing_evidence"),
        f"{label} sample missing_evidence",
    )
    forbidden_claims = _unique_strings(
        value.get("forbidden_claims_found"),
        f"{label} sample forbidden_claims_found",
    )
    component_fields = result_fields[1:]
    missing_response_result = (
        not any(value[name] for name in component_fields) and not forbidden_claims
    )
    if not missing_response_result:
        if value["required_evidence_passed"] != (not missing_evidence):
            raise ValueError(
                f"{label} sample required_evidence_passed is inconsistent"
            )
        if value["forbidden_claims_passed"] != (not forbidden_claims):
            raise ValueError(
                f"{label} sample forbidden_claims_passed is inconsistent"
            )
    expected_passed = all(value[name] for name in component_fields)
    if value["passed"] is not expected_passed:
        raise ValueError(f"{label} sample passed is inconsistent")
    return case_id


def _validate_metrics(value: Any, sample_count: int, label: str) -> dict[str, float]:
    expected_fields = {"sample_count", *_RATE_METRICS}
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise ValueError(f"{label} metrics fields are invalid")
    count = value.get("sample_count")
    if isinstance(count, bool) or not isinstance(count, int) or count != sample_count:
        raise ValueError(f"{label} sample_count is inconsistent")
    return {name: _rate(value.get(name), f"{label} {name}") for name in _RATE_METRICS}


def _validate_derived_results(
    report: dict[str, Any],
    metrics: dict[str, float],
    label: str,
) -> None:
    samples = report["samples"]
    result_fields = {
        "root_cause_hit_rate": "root_cause_passed",
        "required_evidence_pass_rate": "required_evidence_passed",
        "forbidden_claim_free_rate": "forbidden_claims_passed",
        "ticket_priority_match_rate": "ticket_priority_passed",
        "overall_pass_rate": "passed",
    }
    for metric, field in result_fields.items():
        expected = sum(sample[field] for sample in samples) / len(samples)
        if not math.isclose(metrics[metric], expected, abs_tol=1e-12):
            raise ValueError(f"{label} {metric} is inconsistent with samples")
    if report["passed"] is not all(sample["passed"] for sample in samples):
        raise ValueError(f"{label} passed is inconsistent with samples")


def _validate_run(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(_RUN_FIELDS):
        raise ValueError(f"{label} run metadata fields are invalid")
    return {
        name: _stable_text(value.get(name), f"{label} {name}")
        for name in _RUN_FIELDS
    }


def _validate_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _POLICY_FIELDS:
        raise ValueError("gate policy fields are invalid")
    if value.get("schema_version") != 1:
        raise ValueError("gate policy schema_version must be 1")
    minimum = _rate(
        value.get("minimum_overall_pass_rate"),
        "minimum_overall_pass_rate",
    )
    safety = _rate(
        value.get("required_forbidden_claim_free_rate"),
        "required_forbidden_claim_free_rate",
    )
    if minimum < 0.95:
        raise ValueError("minimum_overall_pass_rate cannot be lower than 0.95")
    if safety != 1.0:
        raise ValueError("required_forbidden_claim_free_rate must be 1.0")
    if value.get("require_no_metric_regression") is not True:
        raise ValueError("require_no_metric_regression must be true")
    return {
        "schema_version": 1,
        "minimum_overall_pass_rate": minimum,
        "required_forbidden_claim_free_rate": safety,
        "require_no_metric_regression": True,
    }


def _validate_comparison(
    baseline: ValidatedReport,
    candidate: ValidatedReport,
) -> None:
    if (
        baseline.dataset_id != candidate.dataset_id
        or baseline.dataset_version != candidate.dataset_version
        or baseline.sample_count != candidate.sample_count
        or baseline.case_ids != candidate.case_ids
    ):
        raise ValueError("reports must describe the same dataset and ordered cases")
    if baseline.run == candidate.run:
        raise ValueError("candidate run metadata must differ from baseline")


def _build_checks(
    baseline: dict[str, float],
    candidate: dict[str, float],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    minimum = policy["minimum_overall_pass_rate"]
    safety = policy["required_forbidden_claim_free_rate"]
    checks: list[dict[str, Any]] = [
        {
            "check": "minimum_overall_pass_rate",
            "passed": candidate["overall_pass_rate"] >= minimum,
            "required": minimum,
            "actual": candidate["overall_pass_rate"],
        },
        {
            "check": "required_forbidden_claim_free_rate",
            "passed": candidate["forbidden_claim_free_rate"] == safety,
            "required": safety,
            "actual": candidate["forbidden_claim_free_rate"],
        },
    ]
    checks.extend(
        {
            "check": "no_metric_regression",
            "metric": metric,
            "passed": candidate[metric] >= baseline[metric],
            "baseline": baseline[metric],
            "candidate": candidate[metric],
            "delta": candidate[metric] - baseline[metric],
        }
        for metric in _RATE_METRICS
    )
    return checks


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} is invalid")
    return value


def _stable_text(value: Any, name: str) -> str:
    result = _text(value, name)
    if any(character.isspace() for character in result):
        raise ValueError(f"{name} is invalid")
    return result


def _unique_strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    result = [_text(item, name) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique values")
    return result


def _rate(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite rate")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be a finite rate between 0 and 1")
    return result


def _read_document(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".json":
            document = json.loads(path.read_text(encoding="utf-8"))
        else:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"could not read {path}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain an object")
    return document


def _write_new_decision(path: Path, decision: dict[str, Any]) -> None:
    content = json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    except FileExistsError as exc:
        raise ValueError("output decision already exists") from exc
    except OSError as exc:
        raise ValueError(f"could not write {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare baseline and candidate RCA evaluation reports without rollout."
        ),
    )
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        decision = compare_reports(
            _read_document(args.baseline),
            _read_document(args.candidate),
            _read_document(args.policy),
        )
        _write_new_decision(args.output, decision)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "decision": decision["decision"],
                "next_action": decision["next_action"],
                "automatic_rollout": decision["automatic_rollout"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if decision["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
