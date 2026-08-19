from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from devops_agent_platform.evaluation.runner import (
    main,
    run_benchmark,
    run_deterministic_contract,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_ROOT = PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios"
FIXTURE_INPUT = (
    PROJECT_ROOT / "ops" / "evaluation" / "fixtures" / "minishop-v1-predictions.json"
)


def prediction_document() -> dict[str, object]:
    return {
        "benchmark": {
            "benchmark_version": "1.0",
            "git_commit": "a" * 40,
            "model_provider": "stub",
            "model_name": "deterministic-fixture",
            "temperature": 0,
            "prompt_version": "rca-report-v1",
            "investigation_policy": "fixed_default",
            "scenario_version": "1.0",
            "timestamp": "2026-08-17T12:00:00+08:00",
        },
        "runs": [
            {
                "scenario_id": "payment-error",
                "run_id": "payment-run-001",
                "root_cause": {
                    "service": "payment-service",
                    "type": "application_error",
                    "resource": None,
                },
                "conclusion_status": "CANDIDATE",
                "confidence": 0.8,
                "evidence_ids": ["ev-metric", "ev-log", "ev-trace"],
                "evidence_types": ["METRIC", "LOG", "TRACE"],
                "claims": [
                    {
                        "claim_type": "ROOT_CAUSE",
                        "statement": "payment-service returned an application error",
                        "evidence_ids": ["ev-log", "ev-trace"],
                    }
                ],
                "causal_chain": [
                    {
                        "from_node": "payment-service",
                        "to_node": "checkout-service",
                        "evidence_ids": ["ev-trace"],
                    }
                ],
                "affected_services": ["payment-service", "checkout-service"],
                "tool_calls": [
                    {
                        "tool_type": "metrics.query@v1",
                        "status": "SUCCEEDED",
                        "evidence_ids": ["ev-metric"],
                    },
                    {
                        "tool_type": "logs.query@v1",
                        "status": "SUCCEEDED",
                        "evidence_ids": ["ev-log"],
                    },
                    {
                        "tool_type": "traces.query@v1",
                        "status": "SUCCEEDED",
                        "evidence_ids": ["ev-trace"],
                    },
                    {
                        "tool_type": "runbooks.retrieve@v1",
                        "status": "SUCCEEDED",
                        "evidence_ids": [],
                    },
                ],
                "investigation_steps": 4,
                "llm_calls": 1,
                "latency_ms": 1500,
                "total_tokens": 500,
                "estimated_cost": 0.02,
            }
        ],
    }


def single_scenario_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "scenarios"
    directory.mkdir()
    source = SCENARIO_ROOT / "payment-error.json"
    (directory / source.name).write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return directory


def write_input(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / "predictions.json"
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def test_runner_writes_machine_and_human_readable_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "artifacts" / "benchmark-001"

    result = run_benchmark(
        single_scenario_directory(tmp_path),
        write_input(tmp_path, prediction_document()),
        output,
    )

    stored = json.loads((output / "results.json").read_text(encoding="utf-8"))
    breakdown = json.loads(
        (output / "root-cause-error-breakdown.json").read_text(encoding="utf-8")
    )
    report = (output / "evaluation-report.md").read_text(encoding="utf-8")
    assert result == stored
    assert stored["summary"]["total_runs"] == 1
    assert stored["summary"]["passed_runs"] == 1
    assert "ground_truth" not in stored
    assert breakdown["total_failed_runs"] == 0
    assert breakdown["failure_types"] == {}
    assert "# AIOps Benchmark 评测报告" in report
    assert "Strict RCA Top-1" in report
    assert "Candidate Recall@3" in report
    assert "不等同于真实生产环境或真实 LLM 验收" in report


def test_repository_contract_fixture_scores_all_four_scenarios(
    tmp_path: Path,
) -> None:
    result = run_benchmark(SCENARIO_ROOT, FIXTURE_INPUT, tmp_path / "artifacts")

    assert result["summary"]["total_runs"] == 4
    assert result["summary"]["passed_runs"] == 4
    assert {item["scenario_id"] for item in result["runs"]} == {
        "checkout-latency",
        "deployment-regression",
        "inventory-db-timeout",
        "payment-error",
    }


def test_deterministic_contract_covers_all_extended_scenarios(tmp_path: Path) -> None:
    result = run_deterministic_contract(
        SCENARIO_ROOT,
        tmp_path / "artifacts",
        git_commit="42665bd",
        include_extended=True,
    )

    assert result["contract_fixture"] is True
    assert result["benchmark"]["git_commit"] == "42665bd"
    assert result["summary"]["total_runs"] == 12
    assert result["summary"]["passed_runs"] == 12
    assert result["summary"]["rca_top1_accuracy"] == 1.0
    assert not (tmp_path / "artifacts" / "bad_cases.jsonl").read_text(
        encoding="utf-8"
    )


def test_runner_refuses_to_overwrite_existing_artifacts(tmp_path: Path) -> None:
    scenarios = single_scenario_directory(tmp_path)
    input_path = write_input(tmp_path, prediction_document())
    output = tmp_path / "artifacts"
    run_benchmark(scenarios, input_path, output)

    with pytest.raises(ValueError, match="already exists"):
        run_benchmark(scenarios, input_path, output)


def test_runner_rejects_unknown_scenario(tmp_path: Path) -> None:
    document = prediction_document()
    runs = deepcopy(document["runs"])
    assert isinstance(runs, list)
    runs[0]["scenario_id"] = "unknown-scenario"
    document["runs"] = runs

    with pytest.raises(ValueError, match="unknown scenarios"):
        run_benchmark(
            single_scenario_directory(tmp_path),
            write_input(tmp_path, document),
            tmp_path / "artifacts",
        )


def test_cli_returns_one_when_a_valid_run_fails_the_gate(tmp_path: Path) -> None:
    document = prediction_document()
    runs = deepcopy(document["runs"])
    assert isinstance(runs, list)
    runs[0]["root_cause"] = {
        "service": "inventory-service",
        "type": "dependency_timeout",
        "resource": "postgres",
    }
    document["runs"] = runs
    scenarios = single_scenario_directory(tmp_path)
    input_path = write_input(tmp_path, document)
    output = tmp_path / "artifacts"

    exit_code = main(
        [
            "--scenarios",
            str(scenarios),
            "--input",
            str(input_path),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 1
    result = json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert result["summary"]["passed_runs"] == 0


def test_input_requires_timezone_aware_timestamp(tmp_path: Path) -> None:
    document = prediction_document()
    benchmark = deepcopy(document["benchmark"])
    assert isinstance(benchmark, dict)
    benchmark["timestamp"] = "2026-08-17T12:00:00"
    document["benchmark"] = benchmark

    exit_code = main(
        [
            "--scenarios",
            str(single_scenario_directory(tmp_path)),
            "--input",
            str(write_input(tmp_path, document)),
            "--output",
            str(tmp_path / "artifacts"),
        ]
    )

    assert exit_code == 2
    assert not (tmp_path / "artifacts").exists()
