from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from ops.product.publish_evaluation_dataset import (
    canonical_sha256,
    publish_dataset,
)

PRODUCT_ROOT = Path(__file__).resolve().parents[3] / "ops" / "product"


def read_document(name: str) -> dict:
    path = PRODUCT_ROOT / name
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def approval_document(dataset: dict, candidate: dict, review: dict) -> dict:
    return {
        "schema_version": 1,
        "dataset_id": dataset["dataset_id"],
        "source_version": "v1",
        "target_version": "v2",
        "requested_by": "release-owner",
        "published_at": "2026-08-18T18:00:00+08:00",
        "synthetic": True,
        "candidate_sha256": canonical_sha256(candidate),
        "curation_review_sha256": canonical_sha256(review),
        "reviews": [
            {
                "reviewer_id": "domain-reviewer",
                "role": "domain",
                "approved": True,
                "reviewed_at": "2026-08-18T17:30:00+08:00",
                "notes": "Root cause and evidence labels were checked.",
            },
            {
                "reviewer_id": "privacy-reviewer",
                "role": "privacy",
                "approved": True,
                "reviewed_at": "2026-08-18T17:45:00+08:00",
                "notes": "Identifiers and sensitive values were checked.",
            },
        ],
    }


def inputs() -> tuple[dict, dict, dict]:
    return (
        read_document("evaluation-dataset.example.yml"),
        read_document("evaluation-candidate.example.json"),
        read_document("evaluation-curation-review.example.yml"),
    )


def test_publisher_requires_quorum_and_writes_immutable_provenance(
    tmp_path: Path,
) -> None:
    dataset, candidate, review = inputs()
    original = deepcopy(dataset)
    output = tmp_path / "releases" / "v2"

    manifest = publish_dataset(
        dataset=dataset,
        candidate=candidate,
        curation_review=review,
        approvals=approval_document(dataset, candidate, review),
        output_directory=output,
    )

    assert dataset == original
    assert manifest["version"] == "v2"
    assert manifest["synthetic"] is True
    assert {item["role"] for item in manifest["reviews"]} == {"domain", "privacy"}
    published = yaml.safe_load((output / "dataset.yml").read_text(encoding="utf-8"))
    stored_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert published["version"] == "v2"
    assert stored_manifest == manifest
    assert stored_manifest["provenance"]["published_dataset_sha256"]

    with pytest.raises(ValueError, match="already exists"):
        publish_dataset(
            dataset=dataset,
            candidate=candidate,
            curation_review=review,
            approvals=approval_document(dataset, candidate, review),
            output_directory=output,
        )


@pytest.mark.parametrize("failure", ["self_review", "duplicate", "missing_role"])
def test_publisher_rejects_invalid_reviewer_separation(
    tmp_path: Path,
    failure: str,
) -> None:
    dataset, candidate, review = inputs()
    approvals = approval_document(dataset, candidate, review)
    if failure == "self_review":
        approvals["reviews"][0]["reviewer_id"] = approvals["requested_by"]
        message = "cannot review"
    elif failure == "duplicate":
        approvals["reviews"][1]["reviewer_id"] = approvals["reviews"][0]["reviewer_id"]
        message = "distinct"
    else:
        approvals["reviews"][1]["role"] = "domain"
        message = "domain and privacy"

    with pytest.raises(ValueError, match=message):
        publish_dataset(
            dataset=dataset,
            candidate=candidate,
            curation_review=review,
            approvals=approvals,
            output_directory=tmp_path / failure,
        )
