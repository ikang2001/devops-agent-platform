from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from devops_agent_platform.evaluation.live_runner import (
    LiveLLMConfig,
    run_live_benchmark,
)
from ops.evaluation.build_reference_inputs import build_reference_suite
from ops.simulation.llm_config import (
    apply_compose_environment,
    load_simulation_values,
    resolve_llm_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_ROOT = PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios"
VARIANTS = ("baseline", "change", "topology", "knowledge", "dynamic")
SIMULATION_MAX_RETRIES = 2
TOOL_PREFIXES = {
    "metrics": "metrics.query@v1",
    "logs": "logs.query@v1",
    "traces": "traces.query@v1",
    "changes": "changes.query@v1",
    "topology": "topology.query@v1",
    "knowledge": "knowledge.search@v1",
    "runbooks": "runbooks.retrieve@v1",
}


def _variant_suite(variant: str) -> dict[str, object]:
    suite = build_reference_suite(SCENARIO_ROOT, simulation=True)
    allowed = {
        "baseline": {
            TOOL_PREFIXES[name] for name in ("metrics", "logs", "traces", "runbooks")
        },
        "change": {
            TOOL_PREFIXES[name]
            for name in ("metrics", "changes", "logs", "traces", "runbooks")
        },
        "topology": {
            TOOL_PREFIXES[name]
            for name in ("metrics", "logs", "traces", "topology", "runbooks")
        },
        "knowledge": {
            TOOL_PREFIXES[name]
            for name in ("metrics", "logs", "traces", "knowledge", "runbooks")
        },
        "dynamic": set(TOOL_PREFIXES.values()),
    }[variant]
    for case in suite["cases"]:
        case["tool_calls"] = [
            call for call in case["tool_calls"] if call["tool_type"] in allowed
        ]
        case["investigation_steps"] = len(case["tool_calls"])
    return suite


def _preflight(base_url: str, model: str, api_key: str) -> None:
    response = httpx.get(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    response.raise_for_status()
    if model not in json.dumps(response.json(), ensure_ascii=False):
        raise ValueError(f"configured LLM API does not expose model: {model}")


def _load_completed_summary(path: Path, variant: str) -> dict[str, object]:
    """Load one completed simulation variant without trusting partial output."""

    result_path = path / "results.json"
    document = json.loads(result_path.read_text(encoding="utf-8"))
    execution = document.get("execution")
    if not isinstance(execution, dict):
        raise ValueError(f"{variant} result has no execution metadata")
    if (
        execution.get("simulation") is not True
        or execution.get("synthetic") is not False
        or execution.get("runs_per_scenario") != 5
    ):
        raise ValueError(f"{variant} result is not a complete simulation artifact")
    summary = document.get("summary")
    if not isinstance(summary, dict) or summary.get("total_runs") != 60:
        raise ValueError(f"{variant} result must contain 12 scenarios x 5 runs")
    return summary


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


async def _run_variant(
    variant: str,
    suite_path: Path,
    output: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    provider: str,
    api_style: str,
    input_cost: float,
    output_cost: float,
) -> dict[str, object]:
    return await run_live_benchmark(
        scenario_directory=SCENARIO_ROOT,
        input_path=suite_path,
        output_directory=output,
        git_commit=_git_commit(),
        runs_per_scenario=5,
        config=LiveLLMConfig(
            base_url=base_url,
            api_key=SecretStr(api_key),
            provider=provider,
            model=model,
            api_style=api_style,
            input_cost_per_million=input_cost,
            output_cost_per_million=output_cost,
            allow_insecure_http=urlparse(base_url).scheme == "http",
            max_retries=SIMULATION_MAX_RETRIES,
        ),
        require_real=False,
        investigation_policy=variant,
        max_concurrency=4,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run five local simulation ablation variants"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / "ops" / "simulation" / ".env.example",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "reuse complete variant results and continue after a transient "
            "provider failure"
        ),
    )
    args = parser.parse_args(argv)
    values = load_simulation_values(args.env_file)
    llm = resolve_llm_settings(values)
    apply_compose_environment(llm)
    _preflight(llm.host_base_url, llm.model, llm.api_key)
    if args.output_root.exists() and not args.resume:
        raise ValueError(
            f"simulation ablation output already exists: {args.output_root}"
        )
    args.output_root.mkdir(parents=True, exist_ok=True)

    input_cost = llm.input_cost_per_million
    output_cost = llm.output_cost_per_million
    summaries: dict[str, dict[str, object]] = {}
    for variant in VARIANTS:
        suite_path = args.output_root / f"{variant}-input.json"
        suite_path.write_text(
            json.dumps(_variant_suite(variant), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        existing_result = args.output_root / variant / "results.json"
        if args.resume and existing_result.is_file():
            summaries[variant] = _load_completed_summary(
                args.output_root / variant, variant
            )
            continue
        result = asyncio.run(
            _run_variant(
                variant,
                suite_path,
                args.output_root / variant,
                base_url=llm.host_base_url,
                api_key=llm.api_key,
                model=llm.model,
                provider=llm.provider,
                api_style=llm.api_style,
                input_cost=input_cost,
                output_cost=output_cost,
            )
        )
        summaries[variant] = result["summary"]

    baseline = summaries["baseline"]
    dynamic = summaries["dynamic"]
    gate = (
        float(dynamic["rca_top1_accuracy"]) >= float(baseline["rca_top1_accuracy"])
        and float(dynamic["evidence_recall"]) >= float(baseline["evidence_recall"])
        and float(dynamic["unsupported_claim_rate"])
        <= float(baseline["unsupported_claim_rate"])
        and any(
            float(dynamic[key]) < float(baseline[key])
            for key in ("avg_tool_calls", "avg_tokens", "p95_latency_ms")
        )
    )
    lines = [
        "# 本地生产级仿真消融报告",
        "",
        "本报告使用真实本地 LLM Runtime，五个变体均为 12 场景 × 5 次；"
        "结果标记 `simulation=true`，不代表真实生产签字。",
        "",
        "| 变体 | RCA Top-1 | Evidence Recall | Unsupported Claim | Tool Calls | "
        "Tokens | P95(ms) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in VARIANTS:
        summary = summaries[variant]
        lines.append(
            f"| {variant} | {summary['rca_top1_accuracy']} | "
            f"{summary['evidence_recall']} | {summary['unsupported_claim_rate']} | "
            f"{summary['avg_tool_calls']} | {summary['avg_tokens']} | "
            f"{summary['p95_latency_ms']} |"
        )
    lines.extend(("", f"Dynamic 门禁：{'PASS' if gate else 'FAIL'}", ""))
    (args.output_root / "ablation-report.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    (args.output_root / "simulation-ablation-summary.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "simulation": True,
                "synthetic": False,
                "production_acceptance": False,
                "generated_at": datetime.now(UTC).isoformat(),
                "variants": VARIANTS,
                "llm_provider": llm.provider,
                "llm_model": llm.model,
                "llm_api_style": llm.api_style,
                "llm_input_cost_per_million": llm.input_cost_per_million,
                "llm_output_cost_per_million": llm.output_cost_per_million,
                "llm_pricing_basis": llm.pricing_basis,
                "llm_max_retries": SIMULATION_MAX_RETRIES,
                "dynamic_gate_passed": gate,
                "summaries": summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
