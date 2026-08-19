from __future__ import annotations

import argparse
import json
from pathlib import Path

VARIANTS = ("baseline", "change", "topology", "knowledge", "dynamic")
_METRICS = (
    "rca_top1_accuracy",
    "root_service_accuracy",
    "root_type_accuracy",
    "evidence_precision",
    "evidence_recall",
    "evidence_f1",
    "unsupported_claim_rate",
    "forbidden_claim_rate",
    "causal_chain_f1",
    "blast_radius_f1",
    "tool_selection_accuracy",
    "avg_tool_calls",
    "avg_investigation_steps",
    "redundant_tool_call_rate",
    "avg_llm_calls",
    "avg_tokens",
    "avg_cost",
    "p50_latency_ms",
    "p95_latency_ms",
)


def _load_real_summary(path: Path, name: str) -> dict[str, object]:
    result = json.loads(path.read_text(encoding="utf-8"))
    execution = result.get("execution")
    if not isinstance(execution, dict) or execution.get("synthetic") is not False:
        raise ValueError(f"{name} result is not a real-provider benchmark artifact")
    if execution.get("simulation") is True:
        raise ValueError(f"{name} result is a production-like simulation artifact")
    if execution.get("runs_per_scenario") != 5:
        raise ValueError(f"{name} result must contain exactly five runs per scenario")
    summary = result.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"{name} result has no summary")
    if summary.get("total_runs") != 60:
        raise ValueError(f"{name} result must contain 12 scenarios x 5 runs")
    return summary


def _dynamic_gate(baseline: dict[str, object], dynamic: dict[str, object]) -> bool:
    return (
        float(dynamic["rca_top1_accuracy"])
        >= float(baseline["rca_top1_accuracy"])
        and float(dynamic["evidence_recall"])
        >= float(baseline["evidence_recall"])
        and float(dynamic["unsupported_claim_rate"])
        <= float(baseline["unsupported_claim_rate"])
        and any(
            float(dynamic[metric]) < float(baseline[metric])
            for metric in ("avg_tool_calls", "avg_tokens", "p95_latency_ms")
        )
    )


def render_ablation(paths: dict[str, Path]) -> str:
    missing = [name for name in VARIANTS if name not in paths]
    if missing:
        raise ValueError(f"missing measured variant results: {', '.join(missing)}")
    summaries = {
        name: _load_real_summary(paths[name], name) for name in VARIANTS
    }
    rows = [
        "| " + name + " | " + " | ".join(
            str(summaries[name].get(metric, "N/A")) for metric in _METRICS
        ) + " |"
        for name in VARIANTS
    ]
    headers = " | ".join(_METRICS)
    gate_passed = _dynamic_gate(summaries["baseline"], summaries["dynamic"])
    return "\n".join(
        (
            "# AIOps Benchmark 消融实验报告",
            "",
            (
                "本报告只读取五个变体的真实 Provider `results.json`，"
                "拒绝 synthetic/reference 结果。"
            ),
            "",
            "| 变体 | " + headers + " |",
            "|---|" + "---:|" * len(_METRICS),
            *rows,
            "",
            "## Dynamic 门禁",
            "",
            f"- 结果：{'PASS' if gate_passed else 'FAIL'}",
            (
                "- 条件：RCA Top-1 不低于 Baseline；Evidence Recall 不下降；"
                "Unsupported Claim 不增加；Tool Calls、Token 或 P95 延迟至少一项下降。"
            ),
            "",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render measured real-provider AIOps ablation results"
    )
    for name in VARIANTS:
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = {name: getattr(args, name) for name in VARIANTS}
    report = render_ablation(paths)
    if args.output.exists():
        raise ValueError("output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    summaries = {
        name: _load_real_summary(paths[name], name) for name in VARIANTS
    }
    return 0 if _dynamic_gate(summaries["baseline"], summaries["dynamic"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
