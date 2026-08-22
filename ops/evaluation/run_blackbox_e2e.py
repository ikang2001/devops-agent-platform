"""Run a v0.7 black-box E2E benchmark from public scenarios and runtime snapshots."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from pydantic import SecretStr

from devops_agent_platform.evaluation.blackbox import (
    BlackBoxE2ERunner,
    JsonSnapshotExecutor,
)
from devops_agent_platform.evaluation.live_runner import LiveLLMConfig


def _config(args: argparse.Namespace) -> LiveLLMConfig | None:
    if args.base_url is None:
        return None
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise ValueError(f"missing environment variable {args.api_key_env}")
    return LiveLLMConfig(
        base_url=args.base_url,
        api_key=SecretStr(api_key),
        provider=args.provider,
        model=args.model,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        input_cost_per_million=args.input_cost_per_million,
        output_cost_per_million=args.output_cost_per_million,
        allow_insecure_http=args.allow_insecure_http,
    )


async def _run(args: argparse.Namespace) -> dict[str, object]:
    runner = BlackBoxE2ERunner(
        public_directory=args.public,
        private_directory=args.private,
        executor=JsonSnapshotExecutor(args.runtime_input),
    )
    return await runner.run(
        output_directory=args.output,
        git_commit=args.git_commit,
        config=_config(args),
        runs_per_scenario=args.runs_per_scenario,
        temporary_root=args.temporary_root,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", required=True, type=Path)
    parser.add_argument("--private", required=True, type=Path)
    parser.add_argument("--runtime-input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument(
        "--temporary-root", type=Path, default=Path("D:/DevOpsAgentTemp")
    )
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env", default="DEVOPS_AGENT_BENCHMARK_API_KEY")
    parser.add_argument("--provider", default="unknown")
    parser.add_argument("--model", default="unknown")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--input-cost-per-million", type=float, default=0.0)
    parser.add_argument("--output-cost-per-million", type=float, default=0.0)
    parser.add_argument("--runs-per-scenario", type=int, default=5)
    parser.add_argument("--allow-insecure-http", action="store_true")
    args = parser.parse_args(argv)
    result = asyncio.run(_run(args))
    if "summary" in result:
        print(
            "black-box benchmark completed: "
            f"{result['summary']['total_runs']} runs"
        )
    else:
        print("black-box runtime collection completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
