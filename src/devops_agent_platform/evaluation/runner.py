from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devops_agent_platform.evaluation.schemas import (
    BenchmarkInput,
    BenchmarkMetadata,
    Claim,
    ClaimType,
    ConclusionStatus,
    RCAPrediction,
    ToolCall,
    ToolStatus,
    load_benchmark_input,
    load_ground_truth_catalog,
)
from devops_agent_platform.evaluation.scorer import BenchmarkScorer, summarize_scores

from .reporter import render_evaluation_report

RESULTS_NAME = "results.json"
REPORT_NAME = "evaluation-report.md"
ABLATION_NAME = "ablation-report.md"
BAD_CASES_NAME = "bad_cases.jsonl"
PREDICTIONS_NAME = "predictions.json"


def run_benchmark(
    scenario_directory: Path,
    input_path: Path,
    output_directory: Path,
    *,
    include_extended: bool = False,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    """评分已生成的结构化 RCA 快照，并写出不可静默覆盖的实验资产。"""
    benchmark_input = load_benchmark_input(input_path)
    return run_benchmark_input(
        scenario_directory,
        benchmark_input,
        output_directory,
        include_extended=include_extended,
        scenario_id=scenario_id,
    )


def run_benchmark_input(
    scenario_directory: Path,
    benchmark_input: BenchmarkInput,
    output_directory: Path,
    *,
    include_extended: bool = False,
    scenario_id: str | None = None,
    execution_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """评分内存中的预测快照，供真实模型 Runner 复用同一评分和报告链路。"""
    catalog = load_ground_truth_catalog(
        scenario_directory,
        include_extended=include_extended,
    )
    if scenario_id is not None:
        catalog = tuple(item for item in catalog if item.scenario_id == scenario_id)
        if not catalog:
            raise ValueError(f"unknown scenario: {scenario_id}")
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
    if execution_metadata is not None:
        result["execution"] = execution_metadata
    _write_artifacts(
        output_directory,
        result,
        render_evaluation_report(benchmark_input.benchmark, scores, summary),
        _render_ablation_report(result),
        _render_bad_cases(scores),
        benchmark_input,
    )
    return result


def run_deterministic_contract(
    scenario_directory: Path,
    output_directory: Path,
    *,
    git_commit: str,
    include_extended: bool = True,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    """运行不调用模型的 12 场景合同夹具；结果不能作为模型准确率。"""
    catalog = load_ground_truth_catalog(
        scenario_directory,
        include_extended=include_extended,
    )
    if scenario_id is not None:
        catalog = tuple(item for item in catalog if item.scenario_id == scenario_id)
    if not catalog:
        raise ValueError("no scenario selected for deterministic contract")
    metadata = BenchmarkMetadata(
        benchmark_version="2.0-contract",
        git_commit=git_commit,
        model_provider="stub",
        model_name="deterministic-contract",
        temperature=0,
        top_p=1,
        max_tokens=0,
        prompt_version="no-llm-contract",
        investigation_policy="bounded_dynamic_v1",
        scenario_version=catalog[0].scenario_version,
        timestamp=datetime.now(UTC),
    )
    benchmark_input = BenchmarkInput(
        benchmark=metadata,
        runs=tuple(_contract_prediction(item) for item in catalog),
    )
    scorer = BenchmarkScorer()
    scores = tuple(
        scorer.score(item, prediction)
        for item, prediction in zip(catalog, benchmark_input.runs, strict=True)
    )
    summary = summarize_scores(scores)
    result = {
        "schema_version": "1.0",
        "contract_fixture": True,
        "benchmark": metadata.model_dump(mode="json"),
        "runs": [item.to_dict() for item in scores],
        "summary": summary.to_dict(),
    }
    _write_artifacts(
        output_directory,
        result,
        render_evaluation_report(metadata, scores, summary),
        _render_ablation_report(result),
        _render_bad_cases(scores),
        benchmark_input,
    )
    return result


def _contract_prediction(ground_truth: Any) -> RCAPrediction:
    evidence_ids = tuple(
        f"contract-{ground_truth.scenario_id}-{index}"
        for index, _ in enumerate(ground_truth.required_evidence_types, start=1)
    )
    root = ground_truth.root_cause
    claims = ()
    if root is not None:
        claims = (
            Claim(
                claim_type=ClaimType.ROOT_CAUSE,
                statement=f"{root.service} {root.type}",
                evidence_ids=evidence_ids,
            ),
        )
    chain = tuple(
        edge.model_copy(update={"evidence_ids": evidence_ids})
        for edge in ground_truth.causal_chain
    )
    tool_calls = tuple(
        ToolCall(
            tool_type=tool,
            status=ToolStatus.SUCCEEDED,
            evidence_ids=(evidence_ids[index % len(evidence_ids)],),
        )
        for index, tool in enumerate(ground_truth.expected_tool_types)
    )
    return RCAPrediction(
        scenario_id=ground_truth.scenario_id,
        run_id=f"contract-{ground_truth.scenario_id}",
        root_cause=root,
        conclusion_status=(
            ConclusionStatus.CANDIDATE
            if root is not None
            else ConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE
        ),
        confidence=0.8 if root is not None else 0.0,
        evidence_ids=evidence_ids,
        evidence_types=ground_truth.required_evidence_types,
        claims=claims,
        causal_chain=chain,
        affected_services=ground_truth.affected_services,
        tool_calls=tool_calls,
        investigation_steps=len(tool_calls),
        llm_calls=0,
        latency_ms=0,
        total_tokens=0,
        estimated_cost=0,
    )


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
    ablation_report: str,
    bad_cases: tuple[str, ...],
    benchmark_input: BenchmarkInput,
) -> None:
    paths = tuple(
        output_directory / name
        for name in (
            RESULTS_NAME,
            REPORT_NAME,
            ABLATION_NAME,
            BAD_CASES_NAME,
            PREDICTIONS_NAME,
        )
    )
    if any(path.exists() for path in paths):
        raise ValueError(
            "benchmark output already exists; use a new benchmark directory"
        )
    output_directory.mkdir(parents=True, exist_ok=True)
    results_path = output_directory / RESULTS_NAME
    report_path = output_directory / REPORT_NAME
    results_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(report, encoding="utf-8")
    (output_directory / ABLATION_NAME).write_text(ablation_report, encoding="utf-8")
    (output_directory / BAD_CASES_NAME).write_text("".join(bad_cases), encoding="utf-8")
    (output_directory / PREDICTIONS_NAME).write_text(
        json.dumps(
            benchmark_input.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _render_ablation_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    return "\n".join(
        (
            "# AIOps Benchmark 消融实验报告",
            "",
            "本文件只记录本次 Runner 实际测得的结果；未执行的变体明确标记为未运行。",
            "",
            "## 当前运行实测基线",
            "",
            f"- Runs: {summary['total_runs']}",
            f"- RCA Top-1 Accuracy: {summary['rca_top1_accuracy']:.6f}",
            f"- Evidence F1: {summary['evidence_f1']:.6f}",
            f"- 平均 Tool Calls: {summary['avg_tool_calls']:.6f}",
            f"- 平均 Tokens: {summary['avg_tokens']:.6f}",
            "",
            "## 变体状态",
            "",
            "| 变体 | 状态 |",
            "|---|---|",
            "| A Baseline | 本次输入实际运行 |",
            "| B + Change | 未运行（需要独立输入） |",
            "| C + Topology | 未运行（需要独立输入） |",
            "| D + RAG | 未运行（需要独立输入） |",
            "| E + Dynamic | 未运行（需要独立输入） |",
            "",
        )
    )


def _render_bad_cases(scores: tuple[Any, ...]) -> tuple[str, ...]:
    lines: list[str] = []
    for score in scores:
        if score.passed:
            continue
        categories: list[str] = []
        if not score.rca_exact_match:
            categories.append("WRONG_ROOT_CAUSE")
        if score.evidence_recall < 1:
            categories.append("INSUFFICIENT_EVIDENCE")
        if score.unsupported_claim_rate:
            categories.append("UNSUPPORTED_CLAIM")
        if score.forbidden_claims_found:
            categories.append("FORBIDDEN_CLAIM")
        if score.causal_chain_f1 < 1:
            categories.append("CAUSAL_CHAIN_ERROR")
        if score.blast_radius_f1 < 1:
            categories.append("BLAST_RADIUS_ERROR")
        lines.append(
            json.dumps(
                {
                    "scenario_id": score.scenario_id,
                    "run_id": score.run_id,
                    "categories": categories or ["REGRESSION_GATE"],
                    "metrics": score.to_dict(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
    return tuple(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score structured RCA predictions against isolated Ground Truth.",
    )
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--include-extended",
        action="store_true",
        help="加载 MiniShop-v2 的 12 个场景",
    )
    parser.add_argument("--scenario", type=str, default=None)
    parser.add_argument("--mode", choices=("score", "deterministic"), default="score")
    parser.add_argument("--git-commit", default=None)
    args = parser.parse_args(argv)
    try:
        if args.mode == "deterministic":
            if args.git_commit is None:
                raise ValueError("--git-commit is required in deterministic mode")
            result = run_deterministic_contract(
                args.scenarios,
                args.output,
                git_commit=args.git_commit,
                include_extended=True,
                scenario_id=args.scenario,
            )
        else:
            if args.input is None:
                raise ValueError("--input is required in score mode")
            result = run_benchmark(
                args.scenarios,
                args.input,
                args.output,
                include_extended=args.include_extended,
                scenario_id=args.scenario,
            )
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
        0 if result["summary"]["passed_runs"] == result["summary"]["total_runs"] else 1
    )
