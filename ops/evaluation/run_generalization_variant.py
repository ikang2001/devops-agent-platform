"""Run a real-provider generalization variant from a black-box runtime snapshot."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from pathlib import Path

from pydantic import SecretStr

from devops_agent_platform.evaluation.generalization import (
    GeneralizationVariant,
)
from devops_agent_platform.evaluation.generalization_runner import (
    build_variant_suite,
    transformed_private_directory,
    write_variant_suite,
)
from devops_agent_platform.evaluation.integrity import (
    BenchmarkProvenance,
    hash_directory,
)
from devops_agent_platform.evaluation.live_runner import (
    LiveLLMConfig,
    run_live_benchmark,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _benchmark_version(variant: GeneralizationVariant) -> str:
    """Keep the persisted benchmark identifier within the 32-char contract."""

    return f"0.7-gen-{variant.value}"


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _optional_hash_directory(directory: Path, pattern: str) -> str:
    return hash_directory(directory, pattern=pattern) if directory.exists() else ""


async def _run(args: argparse.Namespace) -> dict[str, object]:
    variant = GeneralizationVariant(args.variant)
    suite, aliases = build_variant_suite(args.runtime_input, variant, seed=args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    variant_input = args.output / "variant-runtime-input.json"
    write_variant_suite(variant_input, suite)
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise ValueError(f"missing environment variable {args.api_key_env}")
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
        max_retries=args.max_retries,
        request_timeout_seconds=args.request_timeout_seconds,
    )
    private_directory = args.private
    private_temp = None
    if aliases:
        private_temp = transformed_private_directory(
            args.private,
            aliases,
            temporary_root=args.temporary_root,
        )
        private_directory = Path(private_temp.name)
    try:
        result = await run_live_benchmark(
            scenario_directory=private_directory,
            input_path=variant_input,
            output_directory=args.output,
            git_commit=args.git_commit,
            runs_per_scenario=args.runs_per_scenario,
            max_concurrency=args.max_concurrency,
            config=config,
            require_real=True,
            required_scenario_count=len(suite.cases),
            benchmark_version=_benchmark_version(variant),
            execution_metadata={
                "generalization_variant": variant.value,
                "variant_seed": args.seed,
                "source_runtime_sha256": hashlib.sha256(
                    args.runtime_input.read_bytes()
                ).hexdigest(),
            },
            continue_on_provider_error=True,
        )
        provenance = BenchmarkProvenance.create(
            run_id=args.output.name,
            benchmark_version=_benchmark_version(variant),
            git_commit=args.git_commit,
            public_scenario_hash=hash_directory(args.public),
            private_ground_truth_hash=hash_directory(private_directory),
            prompt_hash=str(result.get("benchmark", {}).get("prompt_hash", "")),
            taxonomy_hash=_hash_file(
                PROJECT_ROOT / "src/devops_agent_platform/rca_reasoning/taxonomy.py"
            ),
            reasoning_pipeline_hash=_hash_file(
                PROJECT_ROOT / "src/devops_agent_platform/rca_reasoning/pipeline.py"
            ),
            knowledge_hash=_optional_hash_directory(
                PROJECT_ROOT / "ops/knowledge/base", "*.md"
            ),
            runbook_hash=_optional_hash_directory(
                PROJECT_ROOT / "ops/runbooks/base", "*.md"
            ),
            model_provider=config.provider,
            model=config.model,
            temperature=config.temperature,
            runs_per_scenario=args.runs_per_scenario,
        )
        provenance.write(args.output / "benchmark-provenance.json")
        return result
    finally:
        if private_temp is not None:
            private_temp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-input", required=True, type=Path)
    parser.add_argument("--public", required=True, type=Path)
    parser.add_argument("--private", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--variant",
        required=True,
        choices=tuple(GeneralizationVariant),
    )
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--api-style",
        choices=("chat_completions", "responses"),
        default="chat_completions",
    )
    parser.add_argument("--api-key-env", default="DEVOPS_AGENT_BENCHMARK_API_KEY")
    parser.add_argument("--runs-per-scenario", type=int, default=5)
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help="真实 Provider 请求并发上限；默认串行，配额允许时可受控提高",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=120.0,
        help="单次真实 Provider 请求超时；正式 v0.7 批次默认 120 秒",
    )
    parser.add_argument("--input-cost-per-million", type=float, default=0.0)
    parser.add_argument("--output-cost-per-million", type=float, default=0.0)
    parser.add_argument(
        "--temporary-root",
        type=Path,
        default=Path("D:/DevOpsAgentTemp/v07"),
    )
    args = parser.parse_args(argv)
    result = asyncio.run(_run(args))
    print(result["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
