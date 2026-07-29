from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from devops_agent_platform.tools.sanitization import redact_sensitive_text

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_VERSION = re.compile(r"^v([1-9][0-9]*)$")
_PRIORITIES = frozenset({"critical", "high", "medium", "low", "none"})
_REVIEW_KEYS = frozenset(
    {
        "schema_version",
        "candidate_case_id",
        "candidate_feedback_id",
        "approved",
        "privacy_reviewed",
        "target_version",
        "curated_sample",
    }
)
_SAMPLE_KEYS = frozenset(
    {
        "case_id",
        "tenant_id",
        "service",
        "incident_summary",
        "evidence",
        "expected",
    }
)


def curate_dataset(
    dataset: dict[str, Any],
    candidate: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """把一条已人工审核的候选追加到下一版本匿名评测集。"""
    current_version = _validate_dataset(dataset)
    candidate_evidence, internal_ids = _validate_candidate(candidate)
    _validate_review(review, candidate, current_version + 1)
    sample = _build_sample(
        _object(review, "curated_sample"),
        candidate,
        candidate_evidence,
    )
    _reject_internal_id_leak(sample, internal_ids)

    existing_case_ids = {item["case_id"] for item in dataset["samples"]}
    if sample["case_id"] in existing_case_ids:
        raise ValueError("curated case_id already exists in dataset")

    result = deepcopy(dataset)
    result["version"] = f"v{current_version + 1}"
    result["samples"].append(sample)
    return result


def _validate_dataset(dataset: dict[str, Any]) -> int:
    if not isinstance(dataset, dict):
        raise ValueError("evaluation dataset must be an object")
    _identifier(dataset.get("dataset_id"), "dataset_id")
    if dataset.get("privacy") != "anonymized":
        raise ValueError("dataset privacy must be anonymized")
    version = _version_number(dataset.get("version"), "dataset version")
    samples = dataset.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("dataset samples must be a non-empty list")
    case_ids: list[str] = []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("dataset samples must contain objects")
        case_ids.append(_identifier(sample.get("case_id"), "sample case_id"))
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("dataset contains duplicate case_id")
    return version


def _validate_candidate(
    candidate: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], frozenset[str]]:
    if not isinstance(candidate, dict):
        raise ValueError("evaluation candidate must be an object")
    if candidate.get("schema_version") != 1:
        raise ValueError("candidate schema_version must be 1")
    if candidate.get("review_required") is not True:
        raise ValueError("candidate must require review")

    identity_fields = (
        "case_id",
        "workflow_run_id",
        "incident_id",
        "report_id",
        "feedback_id",
    )
    internal_ids = {
        _stable_text(candidate.get(name), f"candidate {name}", 128)
        for name in identity_fields
    }
    required_ids = _unique_stable_strings(
        candidate.get("required_evidence_ids"),
        "candidate required_evidence_ids",
    )
    items = candidate.get("evidence")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("candidate evidence must contain 1 to 100 items")
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("candidate evidence items must be objects")
        evidence_id = _stable_text(
            item.get("evidence_id"),
            "candidate evidence_id",
            64,
        )
        if evidence_id in indexed:
            raise ValueError("candidate contains duplicate evidence_id")
        _stable_text(item.get("source"), "candidate evidence source", 128)
        _bounded_text(item.get("summary"), "candidate evidence summary", 4096)
        indexed[evidence_id] = item
    if required_ids != list(indexed):
        raise ValueError(
            "candidate evidence order must match required_evidence_ids"
        )
    internal_ids.update(required_ids)
    return indexed, frozenset(internal_ids)


def _validate_review(
    review: dict[str, Any],
    candidate: dict[str, Any],
    next_version: int,
) -> None:
    if not isinstance(review, dict) or set(review) != _REVIEW_KEYS:
        raise ValueError("curation review fields are invalid")
    if review.get("schema_version") != 1:
        raise ValueError("review schema_version must be 1")
    if review.get("approved") is not True:
        raise ValueError("curation review must be explicitly approved")
    if review.get("privacy_reviewed") is not True:
        raise ValueError("curation review must confirm privacy review")
    if review.get("candidate_case_id") != candidate.get("case_id"):
        raise ValueError("review candidate_case_id does not match candidate")
    if review.get("candidate_feedback_id") != candidate.get("feedback_id"):
        raise ValueError("review candidate_feedback_id does not match candidate")
    expected_version = f"v{next_version}"
    if review.get("target_version") != expected_version:
        raise ValueError(f"review target_version must be {expected_version}")


def _build_sample(
    curated: dict[str, Any],
    candidate: dict[str, Any],
    candidate_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if set(curated) != _SAMPLE_KEYS:
        raise ValueError("curated_sample fields are invalid")
    incident_summary = _public_text(
        curated.get("incident_summary"),
        "curated incident_summary",
        1024,
    )
    candidate_texts = {
        str(candidate.get("baseline_summary", "")).casefold(),
        str(candidate.get("expected_root_cause", "")).casefold(),
    }
    if incident_summary.casefold() in candidate_texts:
        raise ValueError("curated incident_summary must be manually rewritten")

    evidence = _build_evidence(curated.get("evidence"), candidate_evidence)
    evidence_ids = {item["evidence_id"] for item in evidence}
    return {
        "case_id": _identifier(curated.get("case_id"), "curated case_id"),
        "tenant_id": _identifier(
            curated.get("tenant_id"),
            "curated tenant_id",
        ),
        "service": _identifier(curated.get("service"), "curated service"),
        "incident_summary": incident_summary,
        "evidence": evidence,
        "expected": _build_expected(curated.get("expected"), evidence_ids),
    }


def _build_evidence(
    value: Any,
    candidate_evidence: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != len(candidate_evidence):
        raise ValueError("curated evidence must map every candidate evidence item")
    mapped_ids: list[str] = []
    public_ids: list[str] = []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "candidate_evidence_id",
            "evidence_id",
            "source",
            "summary",
        }:
            raise ValueError("curated evidence fields are invalid")
        candidate_id = _stable_text(
            item.get("candidate_evidence_id"),
            "candidate_evidence_id",
            64,
        )
        if candidate_id not in candidate_evidence:
            raise ValueError(
                "curated evidence references an unknown candidate item"
            )
        public_id = _identifier(item.get("evidence_id"), "curated evidence_id")
        summary = _public_text(
            item.get("summary"),
            "curated evidence summary",
            1024,
        )
        original_summary = str(candidate_evidence[candidate_id].get("summary", ""))
        if summary.casefold() == original_summary.casefold():
            raise ValueError(
                "curated evidence summaries must be manually rewritten"
            )
        mapped_ids.append(candidate_id)
        public_ids.append(public_id)
        result.append(
            {
                "evidence_id": public_id,
                "source": _source(
                    item.get("source"),
                    "curated evidence source",
                ),
                "summary": summary,
            }
        )
    if set(mapped_ids) != set(candidate_evidence):
        raise ValueError("curated evidence mapping is incomplete or duplicated")
    if len(public_ids) != len(set(public_ids)):
        raise ValueError("curated evidence_id values must be unique")
    return result


def _build_expected(value: Any, evidence_ids: set[str]) -> dict[str, Any]:
    expected_keys = {
        "root_cause_category",
        "required_evidence",
        "forbidden_claims",
        "ticket_priority",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("curated expected fields are invalid")
    required = _unique_identifiers(
        value.get("required_evidence"),
        "curated required_evidence",
    )
    if not required or not set(required) <= evidence_ids:
        raise ValueError("required_evidence must reference curated evidence IDs")
    forbidden = _unique_public_texts(
        value.get("forbidden_claims"),
        "curated forbidden_claims",
        512,
    )
    if not forbidden:
        raise ValueError("curated forbidden_claims must not be empty")
    priority = value.get("ticket_priority")
    if priority not in _PRIORITIES:
        raise ValueError("curated ticket_priority is invalid")
    return {
        "root_cause_category": _identifier(
            value.get("root_cause_category"),
            "curated root_cause_category",
        ),
        "required_evidence": required,
        "forbidden_claims": forbidden,
        "ticket_priority": priority,
    }


def _reject_internal_id_leak(value: Any, internal_ids: frozenset[str]) -> None:
    strings = tuple(_walk_strings(value))
    for internal_id in internal_ids:
        needle = internal_id.casefold()
        if any(needle in item.casefold() for item in strings):
            raise ValueError("curated sample contains an internal candidate ID")


def _walk_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str):
        yield value


def _object(value: dict[str, Any], name: str) -> dict[str, Any]:
    result = value.get(name)
    if not isinstance(result, dict):
        raise ValueError(f"{name} must be an object")
    return result


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _source(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SOURCE.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _bounded_text(value: Any, name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{name} is invalid")
    return value


def _stable_text(value: Any, name: str, maximum: int) -> str:
    result = _bounded_text(value, name, maximum)
    if any(character.isspace() for character in result):
        raise ValueError(f"{name} is invalid")
    return result


def _public_text(value: Any, name: str, maximum: int) -> str:
    result = _bounded_text(value, name, maximum)
    if "[redacted" in result.casefold() or redact_sensitive_text(result)[1]:
        raise ValueError(f"{name} contains sensitive or unreviewed text")
    return result


def _unique_stable_strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    result = [_stable_text(item, name, 128) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique values")
    return result


def _unique_identifiers(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    result = [_identifier(item, name) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique values")
    return result


def _unique_public_texts(value: Any, name: str, maximum: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    result = [_public_text(item, name, maximum) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique values")
    return result


def _version_number(value: Any, name: str) -> int:
    if not isinstance(value, str):
        raise ValueError(f"{name} is invalid")
    match = _VERSION.fullmatch(value)
    if match is None:
        raise ValueError(f"{name} is invalid")
    return int(match.group(1))


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


def _write_new_dataset(path: Path, dataset: dict[str, Any]) -> None:
    content = yaml.safe_dump(dataset, allow_unicode=True, sort_keys=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    except FileExistsError as exc:
        raise ValueError("output dataset already exists") from exc
    except OSError as exc:
        raise ValueError(f"could not write {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Curate one reviewed RCA feedback candidate into a new dataset version."
        ),
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        curated = curate_dataset(
            _read_document(args.dataset),
            _read_document(args.candidate),
            _read_document(args.review),
        )
        _write_new_dataset(args.output, curated)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "dataset_id": curated["dataset_id"],
                "version": curated["version"],
                "sample_count": len(curated["samples"]),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
