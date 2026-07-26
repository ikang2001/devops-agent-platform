import importlib.util
import sys
from copy import deepcopy
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_ROOT = PROJECT_ROOT / "ops" / "product"


def load_runner():
    path = PRODUCT_ROOT / "run_evaluation.py"
    spec = importlib.util.spec_from_file_location(
        "product_evaluation_runner",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_yaml(name: str):
    return yaml.safe_load((PRODUCT_ROOT / name).read_text(encoding="utf-8"))


def test_example_evaluation_is_a_complete_passing_baseline() -> None:
    runner = load_runner()

    report = runner.evaluate(
        read_yaml("evaluation-dataset.example.yml"),
        read_yaml("evaluation-responses.example.yml"),
    )

    assert report["passed"] is True
    assert report["metrics"]["sample_count"] == 2
    assert report["metrics"]["overall_pass_rate"] == 1.0
    assert all(item["passed"] for item in report["samples"])


def test_forbidden_claim_and_missing_evidence_fail_regression_gate() -> None:
    runner = load_runner()
    responses = deepcopy(read_yaml("evaluation-responses.example.yml"))
    first = responses["responses"][0]
    first["report_text"] += " Database corruption is also confirmed."
    first["cited_evidence"].remove("ev_trace_001")

    report = runner.evaluate(
        read_yaml("evaluation-dataset.example.yml"),
        responses,
    )

    assert report["passed"] is False
    sample = report["samples"][0]
    assert sample["required_evidence_passed"] is False
    assert sample["forbidden_claims_passed"] is False
    assert sample["missing_evidence"] == ["ev_trace_001"]
    assert sample["forbidden_claims_found"] == ["database corruption"]
