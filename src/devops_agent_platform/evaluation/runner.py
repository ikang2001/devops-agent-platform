from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from devops_agent_platform.evaluation.schemas import (
    BenchmarkInput,
    load_benchmark_input,
    load_ground_truth_catalog,
)
from devops_agent_platform.evaluation.scorer import BenchmarkScorer, summarize_scores

from .reporter import render_evaluation_report

RESULTS_NAME = "results.json"
REPORT_NAME = "evaluation-report.md"


def run_benchmark(
    scenario_directory: Path,
    input_path: Path,
    output_directory: Path,
) -> dict[str, Any]:
    """评分已生成的结构化 RCA 快照，并写出不可静默覆盖的实验资产。"""
    benchmark_input = load_benchmark_input(input_path)
    catalog = load_ground_truth_catalog(scenario_directory)
    ground_truth_by_id = {item.scenario_id: item for item in catalog}
    _validate_suite(benchmark_input, ground_truth_by_id)
    scorer = BenchmarkScorer()
    scores = tuple(
        scorer.score(ground_truth_by_id[item.scenario_id], item)
        for item in benchmark_input.runs
    )
    summary = summarize_scores(scores)
    result = {
        "schema_version": "1.0",
        "benchmark": benchmark_input.benchmark.model_dump(mode="json"),
        "runs": [item.to_dict() for item in scores],
        "summary": summary.to_dict(),
    }
    _write_artifacts(
        output_directory,
        result,
        render_evaluation_report(benchmark_input.benchmark, scores, summary),
    )
    return result


def _validate_suite(
    benchmark_input: BenchmarkInput,
    ground_truth_by_id: dict[str, Any],
) -> None:
    expected_ids = set(ground_truth_by_id)
    actual_ids = {item.scenario_id for item in benchmark_input.runs}
    unknown = sorted(actual_ids - expected_ids)
    if unknown:
        names = ", ".join(unknown)
        raise ValueError(f"benchmark input contains unknown scenarios: {names}")
    missing = sorted(expected_ids - actual_ids)
    if missing:
        raise ValueError(f"benchmark input is missing scenarios: {', '.join(missing)}")
    versions = {item.scenario_version for item in ground_truth_by_id.values()}
    if versions != {benchmark_input.benchmark.scenario_version}:
        raise ValueError("benchmark scenario_version does not match the manifests")


def _write_artifacts(
    output_directory: Path,
    result: dict[str, Any],
    report: str,
) -> None:
    results_path = output_directory / RESULTS_NAME
    report_path = output_directory / REPORT_NAME
    if results_path.exists() or report_path.exists():
        raise ValueError(
            "benchmark output already exists; use a new benchmark directory"
        )
    output_directory.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(report, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score structured RCA predictions against isolated Ground Truth.",
    )
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_benchmark(args.scenarios, args.input, args.output)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "passed": result["summary"]["passed_runs"]
                == result["summary"]["total_runs"],
                "runs": result["summary"]["total_runs"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return (
        0
        if result["summary"]["passed_runs"] == result["summary"]["total_runs"]
        else 1
    )
