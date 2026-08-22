"""Run MiniShop public scenarios through the generic v0.7 black-box runner."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from blackbox_executor import MiniShopBlackBoxExecutor
from pydantic import SecretStr
from run_e2e import E2EConfig

from devops_agent_platform.evaluation.blackbox import BlackBoxE2ERunner
from devops_agent_platform.evaluation.live_runner import LiveLLMConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", default="DEVOPS_AGENT_BENCHMARK_API_KEY")
    parser.add_argument("--runs-per-scenario", type=int, default=5)
    parser.add_argument("--scenario-id")
    parser.add_argument(
        "--temporary-root", type=Path, default=Path("D:/DevOpsAgentTemp")
    )
    args = parser.parse_args()
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise ValueError(f"missing environment variable {args.api_key_env}")
    config = LiveLLMConfig(
        base_url=args.base_url,
        api_key=SecretStr(api_key),
        provider=args.provider,
        model=args.model,
    )
    executor = MiniShopBlackBoxExecutor(E2EConfig())
    try:
        result = asyncio.run(
            BlackBoxE2ERunner(
                public_directory=args.public,
                private_directory=args.private,
                executor=executor,
            ).run(
                output_directory=args.output,
                git_commit=args.git_commit,
                config=config,
                runs_per_scenario=args.runs_per_scenario,
                scenario_id=args.scenario_id,
                temporary_root=args.temporary_root,
            )
        )
    finally:
        executor.close()
    print(result["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
