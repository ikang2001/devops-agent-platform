"""Run the five real-provider RCA benchmark variants and enforce the dynamic gate."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from pydantic import SecretStr

from devops_agent_platform.evaluation.live_runner import (
    LiveLLMConfig,
    run_live_benchmark,
)

from .ablation import VARIANTS, _dynamic_gate, _load_real_summary, render_ablation


async def run_suite(args: argparse.Namespace) -> int:
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise ValueError(f"missing API key environment variable: {args.api_key_env}")
    inputs = {
        name: Path(value)
        for name, value in (item.split("=", 1) for item in args.variant_input)
    }
    missing = [name for name in VARIANTS if name not in inputs]
    if missing:
        raise ValueError(f"missing variant inputs: {', '.join(missing)}")
    config = LiveLLMConfig(
        base_url=args.base_url,
        api_key=SecretStr(api_key),
        provider=args.provider,
        model=args.model,
        api_style=args.api_style,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        input_cost_per_million=args.input_cost_per_million,
        output_cost_per_million=args.output_cost_per_million,
    )
    if not 1 <= args.max_concurrency <= 20:
        raise ValueError("max_concurrency must be between 1 and 20")
    output_root = Path(args.output_root)
    output_paths: dict[str, Path] = {}
    for name in VARIANTS:
        output = output_root / name
        await run_live_benchmark(
            scenario_directory=Path(args.scenarios),
            input_path=inputs[name],
            output_directory=output,
            git_commit=args.git_commit,
            runs_per_scenario=5,
            config=config,
            require_real=True,
            investigation_policy=name,
            max_concurrency=args.max_concurrency,
        )
        output_paths[name] = output / "results.json"
    report = render_ablation(output_paths)
    report_path = output_root / "ablation-report.md"
    report_path.write_text(report, encoding="utf-8")
    baseline = _load_real_summary(output_paths["baseline"], "baseline")
    dynamic = _load_real_summary(output_paths["dynamic"], "dynamic")
    return 0 if _dynamic_gate(baseline, dynamic) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run 12x5 real-provider RCA ablation benchmark"
    )
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", default="DEVOPS_AGENT_BENCHMARK_API_KEY")
    parser.add_argument(
        "--api-style",
        choices=("chat_completions", "responses"),
        default="chat_completions",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--input-cost-per-million", type=float, required=True)
    parser.add_argument("--output-cost-per-million", type=float, required=True)
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help=(
            "maximum concurrent provider requests; keep at 1 unless the provider "
            "quota allows more"
        ),
    )
    parser.add_argument(
        "--variant-input",
        action="append",
        required=True,
        help=(
            "repeat five times as variant=path "
            "(baseline/change/topology/knowledge/dynamic)"
        ),
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run_suite(args))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
