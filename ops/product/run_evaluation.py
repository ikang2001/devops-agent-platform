from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SampleResult:
    case_id: str
    passed: bool
    root_cause_passed: bool
    required_evidence_passed: bool
    forbidden_claims_passed: bool
    ticket_priority_passed: bool
    missing_evidence: tuple[str, ...]
    forbidden_claims_found: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["missing_evidence"] = list(self.missing_evidence)
        result["forbidden_claims_found"] = list(self.forbidden_claims_found)
        return result


def evaluate(
    dataset: dict[str, Any],
    responses: dict[str, Any],
) -> dict[str, Any]:
    """按稳定 Ground Truth 契约评估一批结构化 RCA 响应。"""
    samples = _indexed_items(dataset, "samples")
    actual = _indexed_items(responses, "responses")
    if responses.get("dataset_id") != dataset.get("dataset_id"):
        raise ValueError("response dataset_id does not match dataset")
    if responses.get("dataset_version") != dataset.get("version"):
        raise ValueError("response dataset_version does not match dataset version")
    unknown = sorted(set(actual) - set(samples))
    if unknown:
        raise ValueError(f"responses contain unknown case_ids: {', '.join(unknown)}")

    results = tuple(
        _evaluate_sample(case_id, sample, actual.get(case_id))
        for case_id, sample in samples.items()
    )
    count = len(results)
    if count == 0:
        raise ValueError("evaluation dataset must contain samples")
    metrics = {
        "sample_count": count,
        "root_cause_hit_rate": _rate(
            results,
            "root_cause_passed",
        ),
        "required_evidence_pass_rate": _rate(
            results,
            "required_evidence_passed",
        ),
        "forbidden_claim_free_rate": _rate(
            results,
            "forbidden_claims_passed",
        ),
        "ticket_priority_match_rate": _rate(
            results,
            "ticket_priority_passed",
        ),
        "overall_pass_rate": sum(item.passed for item in results) / count,
    }
    return {
        "schema_version": "1.0",
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "run": _validated_run_metadata(responses.get("run")),
        "passed": all(item.passed for item in results),
        "metrics": metrics,
        "samples": [item.to_dict() for item in results],
    }


def _evaluate_sample(
    case_id: str,
    sample: dict[str, Any],
    response: dict[str, Any] | None,
) -> SampleResult:
    expected = sample.get("expected")
    if not isinstance(expected, dict):
        raise ValueError(f"sample {case_id} expected must be an object")
    required = _string_list(
        expected.get("required_evidence"),
        f"sample {case_id} required_evidence",
    )
    forbidden = _string_list(
        expected.get("forbidden_claims"),
        f"sample {case_id} forbidden_claims",
    )
    if response is None:
        return SampleResult(
            case_id=case_id,
            passed=False,
            root_cause_passed=False,
            required_evidence_passed=False,
            forbidden_claims_passed=False,
            ticket_priority_passed=False,
            missing_evidence=tuple(required),
            forbidden_claims_found=(),
        )

    cited = _string_list(
        response.get("cited_evidence"),
        f"response {case_id} cited_evidence",
    )
    report_text = response.get("report_text")
    if not isinstance(report_text, str) or not report_text.strip():
        raise ValueError(f"response {case_id} report_text is invalid")
    normalized_report = report_text.casefold()
    missing = tuple(item for item in required if item not in set(cited))
    forbidden_found = tuple(
        claim for claim in forbidden if claim.casefold() in normalized_report
    )
    root_passed = response.get("root_cause_category") == expected.get(
        "root_cause_category"
    )
    priority_passed = response.get("ticket_priority") == expected.get("ticket_priority")
    passed = root_passed and not missing and not forbidden_found and priority_passed
    return SampleResult(
        case_id=case_id,
        passed=passed,
        root_cause_passed=root_passed,
        required_evidence_passed=not missing,
        forbidden_claims_passed=not forbidden_found,
        ticket_priority_passed=priority_passed,
        missing_evidence=missing,
        forbidden_claims_found=forbidden_found,
    )


def _indexed_items(
    document: dict[str, Any],
    field_name: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("evaluation document must be an object")
    items = document.get(field_name)
    if not isinstance(items, list):
        raise ValueError(f"{field_name} must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} entries must be objects")
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id != case_id.strip():
            raise ValueError(f"{field_name} case_id is invalid")
        if case_id in indexed:
            raise ValueError(f"duplicate case_id: {case_id}")
        indexed[case_id] = item
    return indexed


def _string_list(value: Any, field_name: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not all(
            isinstance(item, str) and item and item == item.strip() for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{field_name} must be a unique string list")
    return value


def _validated_run_metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("run metadata must be an object")
    required = {
        "prompt_version",
        "model_version",
        "retrieval_version",
    }
    if set(value) != required or not all(
        isinstance(item, str) and item and item == item.strip()
        for item in value.values()
    ):
        raise ValueError(
            "run metadata must contain prompt, model, and retrieval versions"
        )
    return {name: value[name] for name in sorted(required)}


def _rate(results: tuple[SampleResult, ...], field_name: str) -> float:
    return sum(bool(getattr(item, field_name)) for item in results) / len(results)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not read {path}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate structured RCA responses against Ground Truth.",
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--responses", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = evaluate(
            _read_yaml(args.dataset),
            _read_yaml(args.responses),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "overall_pass_rate": (report["metrics"]["overall_pass_rate"]),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
