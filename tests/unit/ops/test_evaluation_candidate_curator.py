import importlib.util
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_ROOT = PROJECT_ROOT / "ops" / "product"


def load_curator():
    path = PRODUCT_ROOT / "curate_evaluation_candidate.py"
    spec = importlib.util.spec_from_file_location(
        "evaluation_candidate_curator",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_evaluation_runner():
    path = PRODUCT_ROOT / "run_evaluation.py"
    spec = importlib.util.spec_from_file_location(
        "curated_dataset_evaluation_runner",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_document(name: str):
    path = PRODUCT_ROOT / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_example_review_creates_next_anonymized_dataset_version() -> None:
    curator = load_curator()
    dataset = read_document("evaluation-dataset.example.yml")
    candidate = read_document("evaluation-candidate.example.json")
    review = read_document("evaluation-curation-review.example.yml")

    curated = curator.curate_dataset(dataset, candidate, review)

    assert dataset["version"] == "v1"
    assert len(dataset["samples"]) == 2
    assert curated["version"] == "v2"
    assert len(curated["samples"]) == 3
    sample = curated["samples"][-1]
    assert sample["case_id"] == "inventory_pool_exhaustion_002"
    assert sample["expected"]["required_evidence"] == [
        "ev_metrics_003",
        "ev_logs_003",
    ]
    serialized = yaml.safe_dump(sample)
    for internal_id in (
        candidate["case_id"],
        candidate["workflow_run_id"],
        candidate["incident_id"],
        candidate["report_id"],
        candidate["feedback_id"],
        *candidate["required_evidence_ids"],
    ):
        assert internal_id not in serialized


def test_curated_dataset_is_consumed_by_existing_evaluation_runner() -> None:
    curator = load_curator()
    runner = load_evaluation_runner()
    curated = curator.curate_dataset(
        read_document("evaluation-dataset.example.yml"),
        read_document("evaluation-candidate.example.json"),
        read_document("evaluation-curation-review.example.yml"),
    )
    responses = read_document("evaluation-responses.example.yml")
    responses["dataset_version"] = "v2"
    responses["responses"].append(
        {
            "case_id": "inventory_pool_exhaustion_002",
            "root_cause_category": (
                "database_connection_pool_exhaustion"
            ),
            "cited_evidence": ["ev_metrics_003", "ev_logs_003"],
            "report_text": (
                "Anonymized metrics and logs support connection pool "
                "exhaustion without asserting corruption or leakage."
            ),
            "ticket_priority": "high",
        }
    )

    report = runner.evaluate(curated, responses)

    assert report["passed"] is True
    assert report["dataset_version"] == "v2"
    assert report["metrics"]["sample_count"] == 3


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("approved", False, "explicitly approved"),
        ("privacy_reviewed", False, "confirm privacy review"),
        ("target_version", "v3", "target_version must be v2"),
        ("candidate_feedback_id", "rcf_other", "does not match"),
    ),
)
def test_review_gate_fails_closed(field: str, value, message: str) -> None:
    curator = load_curator()
    review = read_document("evaluation-curation-review.example.yml")
    review[field] = value

    with pytest.raises(ValueError, match=message):
        curator.curate_dataset(
            read_document("evaluation-dataset.example.yml"),
            read_document("evaluation-candidate.example.json"),
            review,
        )


def test_curated_evidence_must_map_every_candidate_item_once() -> None:
    curator = load_curator()
    review = read_document("evaluation-curation-review.example.yml")
    evidence = review["curated_sample"]["evidence"]
    evidence[1]["candidate_evidence_id"] = evidence[0][
        "candidate_evidence_id"
    ]

    with pytest.raises(ValueError, match="incomplete or duplicated"):
        curator.curate_dataset(
            read_document("evaluation-dataset.example.yml"),
            read_document("evaluation-candidate.example.json"),
            review,
        )


def test_curated_text_must_be_rewritten_and_free_of_sensitive_values() -> None:
    curator = load_curator()
    candidate = read_document("evaluation-candidate.example.json")
    unchanged = read_document("evaluation-curation-review.example.yml")
    unchanged["curated_sample"]["incident_summary"] = candidate[
        "baseline_summary"
    ]

    with pytest.raises(ValueError, match="manually rewritten"):
        curator.curate_dataset(
            read_document("evaluation-dataset.example.yml"),
            candidate,
            unchanged,
        )

    sensitive = read_document("evaluation-curation-review.example.yml")
    sensitive["curated_sample"]["incident_summary"] += (
        " password=do-not-persist"
    )
    with pytest.raises(ValueError, match="sensitive or unreviewed"):
        curator.curate_dataset(
            read_document("evaluation-dataset.example.yml"),
            candidate,
            sensitive,
        )


def test_curated_sample_rejects_internal_candidate_ids() -> None:
    curator = load_curator()
    candidate = read_document("evaluation-candidate.example.json")
    review = read_document("evaluation-curation-review.example.yml")
    review["curated_sample"]["incident_summary"] += (
        f" Internal reference {candidate['incident_id']}."
    )

    with pytest.raises(ValueError, match="internal candidate ID"):
        curator.curate_dataset(
            read_document("evaluation-dataset.example.yml"),
            candidate,
            review,
        )


def test_cli_writes_new_file_once_and_refuses_overwrite(tmp_path: Path) -> None:
    curator = load_curator()
    output = tmp_path / "evaluation-dataset-v2.yml"
    args = [
        "--dataset",
        str(PRODUCT_ROOT / "evaluation-dataset.example.yml"),
        "--candidate",
        str(PRODUCT_ROOT / "evaluation-candidate.example.json"),
        "--review",
        str(PRODUCT_ROOT / "evaluation-curation-review.example.yml"),
        "--output",
        str(output),
    ]

    assert curator.main(args) == 0
    written = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert written["version"] == "v2"
    before = output.read_bytes()

    assert curator.main(args) == 2
    assert output.read_bytes() == before


def test_input_dataset_is_not_mutated_on_failure() -> None:
    curator = load_curator()
    dataset = read_document("evaluation-dataset.example.yml")
    original = deepcopy(dataset)
    review = read_document("evaluation-curation-review.example.yml")
    review["approved"] = False

    with pytest.raises(ValueError):
        curator.curate_dataset(
            dataset,
            read_document("evaluation-candidate.example.json"),
            review,
        )

    assert dataset == original
