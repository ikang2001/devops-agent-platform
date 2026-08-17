from __future__ import annotations

import json
from pathlib import Path

import pytest

from devops_agent_platform.evaluation.schemas import (
    ScenarioGroundTruth,
    load_ground_truth_catalog,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_ROOT = PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios"


def test_existing_minishop_manifests_form_a_scoring_catalog() -> None:
    catalog = load_ground_truth_catalog(SCENARIO_ROOT)

    assert {item.scenario_id for item in catalog} == {
        "checkout-latency",
        "inventory-db-timeout",
        "payment-error",
    }
    for item in catalog:
        assert item.required_evidence_types
        assert item.causal_chain
        assert item.affected_services
        assert item.expected_tool_types
        assert item.forbidden_tool_types


def test_manifest_adapter_rejects_missing_benchmark_ground_truth() -> None:
    document = json.loads(
        (SCENARIO_ROOT / "payment-error.json").read_text(encoding="utf-8")
    )
    del document["ground_truth"]["required_evidence_types"]

    with pytest.raises(ValueError, match="required_evidence_types"):
        ScenarioGroundTruth.from_manifest(document)


def test_catalog_rejects_duplicate_scenario_ids(tmp_path: Path) -> None:
    content = (SCENARIO_ROOT / "payment-error.json").read_text(encoding="utf-8")
    (tmp_path / "first.json").write_text(content, encoding="utf-8")
    (tmp_path / "second.json").write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate scenario_id"):
        load_ground_truth_catalog(tmp_path)
