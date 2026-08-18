from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from ops.product.curate_evaluation_candidate import curate_dataset

_ROLES = frozenset({"domain", "privacy"})


def publish_dataset(
    *,
    dataset: dict[str, Any],
    candidate: dict[str, Any],
    curation_review: dict[str, Any],
    approvals: dict[str, Any],
    output_directory: Path,
) -> dict[str, Any]:
    """经双人签字后发布新的不可变数据集目录和来源 Manifest。"""
    curated = curate_dataset(dataset, candidate, curation_review)
    candidate_hash = canonical_sha256(candidate)
    review_hash = canonical_sha256(curation_review)
    reviewers = _validate_approvals(
        approvals,
        dataset=dataset,
        curated=curated,
        candidate_hash=candidate_hash,
        review_hash=review_hash,
    )
    dataset_bytes = yaml.safe_dump(
        curated,
        allow_unicode=True,
        sort_keys=False,
    ).encode()
    dataset_hash = sha256(dataset_bytes).hexdigest()
    manifest = {
        "schema_version": "1.0",
        "release_id": (
            f"{curated['dataset_id']}:{curated['version']}:{dataset_hash[:12]}"
        ),
        "dataset_id": curated["dataset_id"],
        "source_version": dataset["version"],
        "version": curated["version"],
        "synthetic": approvals["synthetic"],
        "published_at": approvals["published_at"],
        "requested_by": approvals["requested_by"],
        "sample_count": len(curated["samples"]),
        "provenance": {
            "source_dataset_sha256": canonical_sha256(dataset),
            "candidate_sha256": candidate_hash,
            "curation_review_sha256": review_hash,
            "published_dataset_sha256": dataset_hash,
        },
        "reviews": reviewers,
    }
    _write_release(output_directory, dataset_bytes, manifest)
    return manifest


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return sha256(encoded).hexdigest()


def _validate_approvals(
    approvals: dict[str, Any],
    *,
    dataset: dict[str, Any],
    curated: dict[str, Any],
    candidate_hash: str,
    review_hash: str,
) -> list[dict[str, str]]:
    expected_keys = {
        "schema_version",
        "dataset_id",
        "source_version",
        "target_version",
        "requested_by",
        "published_at",
        "synthetic",
        "candidate_sha256",
        "curation_review_sha256",
        "reviews",
    }
    if not isinstance(approvals, dict) or set(approvals) != expected_keys:
        raise ValueError("dataset release approval fields are invalid")
    if approvals["schema_version"] != 1:
        raise ValueError("dataset release approval schema_version must be 1")
    if (
        approvals["dataset_id"] != dataset["dataset_id"]
        or approvals["source_version"] != dataset["version"]
        or approvals["target_version"] != curated["version"]
    ):
        raise ValueError("dataset release versions do not match curated output")
    if approvals["candidate_sha256"] != candidate_hash:
        raise ValueError("candidate SHA-256 does not match release approval")
    if approvals["curation_review_sha256"] != review_hash:
        raise ValueError("curation review SHA-256 does not match release approval")
    requested_by = _identity(approvals["requested_by"], "requested_by")
    if not isinstance(approvals["synthetic"], bool):
        raise ValueError("dataset release synthetic must be a boolean")
    _aware_timestamp(approvals["published_at"])
    raw_reviews = approvals["reviews"]
    if not isinstance(raw_reviews, list) or not 2 <= len(raw_reviews) <= 10:
        raise ValueError("dataset release requires between 2 and 10 reviews")
    reviews: list[dict[str, str]] = []
    for value in raw_reviews:
        if not isinstance(value, dict) or set(value) != {
            "reviewer_id",
            "role",
            "approved",
            "reviewed_at",
            "notes",
        }:
            raise ValueError("dataset reviewer fields are invalid")
        reviewer_id = _identity(value["reviewer_id"], "reviewer_id")
        role = value["role"]
        if role not in _ROLES or value["approved"] is not True:
            raise ValueError("dataset review role or decision is invalid")
        reviewed_at = _aware_timestamp(value["reviewed_at"])
        notes = value["notes"]
        if not isinstance(notes, str) or not 1 <= len(notes) <= 1024:
            raise ValueError("dataset review notes are invalid")
        reviews.append(
            {
                "reviewer_id": reviewer_id,
                "role": role,
                "reviewed_at": reviewed_at,
                "notes": notes,
            }
        )
    reviewer_ids = {item["reviewer_id"] for item in reviews}
    if len(reviewer_ids) != len(reviews):
        raise ValueError("dataset reviewers must be distinct")
    if requested_by in reviewer_ids:
        raise ValueError("dataset release requester cannot review their own release")
    if {item["role"] for item in reviews} != _ROLES:
        raise ValueError("dataset release requires domain and privacy reviews")
    return reviews


def _identity(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or value != value.strip()
        or any(character.isspace() for character in value)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field_name} is invalid")
    return value


def _aware_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("review timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("review timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("review timestamp must include timezone information")
    return parsed.isoformat()


def _write_release(
    output_directory: Path,
    dataset_bytes: bytes,
    manifest: dict[str, Any],
) -> None:
    if output_directory.exists():
        raise ValueError("dataset release directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}-", dir=output_directory.parent
        )
    )
    try:
        (temporary / "dataset.yml").write_bytes(dataset_bytes)
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output_directory)
    except OSError as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise ValueError("could not publish dataset release") from exc


def _read_document(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"could not read {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish a quorum-reviewed dataset")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--curation-review", type=Path, required=True)
    parser.add_argument("--release-approvals", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = publish_dataset(
            dataset=_read_document(args.dataset),
            candidate=_read_document(args.candidate),
            curation_review=_read_document(args.curation_review),
            approvals=_read_document(args.release_approvals),
            output_directory=args.output_directory,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
