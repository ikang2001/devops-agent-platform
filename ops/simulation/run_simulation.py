from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
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
from ops.load.run_load import evaluate_end_to_end_gate
from ops.simulation.llm_config import (
    apply_compose_environment,
    load_simulation_values,
    resolve_llm_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_COMPOSE = PROJECT_ROOT / "ops" / "reference-staging" / "docker-compose.yml"
SIMULATION_COMPOSE = PROJECT_ROOT / "ops" / "simulation" / "docker-compose.yml"
DEFAULT_ENV_FILE = PROJECT_ROOT / "ops" / "simulation" / ".env.example"
SCENARIO_ROOT = PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios"
PROJECT_NAME = "devops-agent-simulation"
SIMULATION_MAX_RETRIES = 2


def _default_output_root() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("D:/DevOpsAgentSimulation") / timestamp


def _setting(values: dict[str, str], name: str, default: str | None = None) -> str:
    value = values.get(name) or default
    if value is None or not value.strip():
        raise ValueError(f"missing simulation setting: {name}")
    return value.strip()


def _preflight_llm(base_url: str, model: str, api_key: str) -> None:
    headers = {"Authorization": f"Bearer {api_key}"}
    endpoint = f"{base_url.rstrip('/')}/models"
    try:
        response = httpx.get(endpoint, headers=headers, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            "真实本地 LLM Runtime 不可用；仿真禁止回退到 provider-mock。"
        ) from exc
    document = response.json()
    serialized = json.dumps(document, ensure_ascii=False)
    if model not in serialized:
        raise RuntimeError(f"configured LLM API does not expose model: {model}")


def _compose_command(env_file: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        PROJECT_NAME,
        "--env-file",
        str(env_file),
        "-f",
        str(REFERENCE_COMPOSE),
        "-f",
        str(SIMULATION_COMPOSE),
        *arguments,
    ]


def _run(command: list[str], *, timeout: int = 1800) -> None:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}")


def _wait_http(url: str, *, timeout_seconds: int = 300) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=3)
            response.raise_for_status()
            return
        except httpx.HTTPError as exc:
            last_error = str(exc)
            time.sleep(1)
    raise RuntimeError(f"service did not become ready: {url} ({last_error})")


def _probe_ticketing(url: str, output: Path) -> dict[str, object]:
    payload = {
        "title": "Production-like simulation incident",
        "description": "Persistent Ticketing idempotency acceptance.",
    }
    headers = {"Idempotency-Key": "simulation-ticket-acceptance-v1"}
    with httpx.Client(timeout=10) as client:
        first = client.post(url, headers=headers, json=payload)
        first.raise_for_status()
        second = client.post(url, headers=headers, json=payload)
        second.raise_for_status()
    first_document = first.json()
    second_document = second.json()
    passed = (
        first_document.get("external_ticket_id")
        == second_document.get("external_ticket_id")
        and second_document.get("idempotent") is True
    )
    report = {
        "schema_version": "1.0",
        "simulation": True,
        "synthetic": False,
        "production_acceptance": False,
        "passed": passed,
        "external_ticket_id": first_document.get("external_ticket_id"),
        "idempotent_replay": second_document.get("idempotent"),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not passed:
        raise RuntimeError("simulation Ticketing idempotency acceptance failed")
    return report


def _mark_load_simulation(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    document["simulation"] = True
    document["production_acceptance"] = False
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _attach_completion_probe(load_path: Path, completion_probe_path: Path) -> None:
    """Attach the post-load RCA completion measurement to every load profile.

    The load runner intentionally cannot know how many RCA workflows should be
    created for a capacity sample.  The completion probe runs immediately after
    the sample and is therefore the authoritative end-to-end measurement for
    this local simulation.  Keeping the value in the load artifact makes the
    gate auditable without pretending that every alert starts a new workflow.
    """
    load_document = json.loads(load_path.read_text(encoding="utf-8"))
    completion_document = json.loads(
        completion_probe_path.read_text(encoding="utf-8")
    )
    completion_rate = completion_document.get("completion_rate")
    if not isinstance(completion_rate, int | float):
        raise ValueError("completion probe did not produce a numeric completion rate")

    for profile in load_document.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        profile["completion_rate"] = float(completion_rate)
        profile["completion_measurement_source"] = "rca-completion-probe"
        profile["completion_requested_workflows"] = completion_document.get(
            "requested_workflows"
        )
        profile["completion_completed_workflows"] = completion_document.get(
            "completed_workflows"
        )
        profile["end_to_end_gate"] = evaluate_end_to_end_gate(profile)

    load_document["completion_probe"] = {
        "artifact": completion_probe_path.name,
        "completion_rate": float(completion_rate),
        "requested_workflows": completion_document.get("requested_workflows"),
        "completed_workflows": completion_document.get("completed_workflows"),
        "measurement_source": "platform-prometheus-metrics",
    }
    load_path.write_text(
        json.dumps(load_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _run_capacity_load(
    *,
    output_root: Path,
    agent_url: str,
    duration_seconds: float,
) -> Path:
    if shutil.which("k6"):
        load_directory = output_root / "load"
        _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PROJECT_ROOT / "ops/load/run-load-matrix.ps1"),
                "-BaseUrl",
                agent_url,
                "-OutputDirectory",
                str(load_directory),
                "-Duration",
                f"{duration_seconds}s",
                "-TargetLabel",
                "production-like-simulation",
            ]
        )
        return load_directory / "load-report.json"

    load_path = output_root / "load-report.json"
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "ops/load/run_load.py"),
            "--mode",
            "http-live",
            "--target-url",
            agent_url,
            "--duration-seconds",
            str(duration_seconds),
            "--non-synthetic",
            "--output",
            str(load_path),
        ]
    )
    return load_path


def _run_completion_probe(
    *,
    output_root: Path,
    agent_url: str,
    oidc_token_url: str,
    webhook_secret: str,
    count: int,
) -> Path:
    output = output_root / "rca-completion-probe.json"
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "ops/simulation/run_rca_completion_probe.py"),
            "--agent-url",
            agent_url,
            "--oidc-token-url",
            oidc_token_url,
            "--webhook-secret",
            webhook_secret,
            "--count",
            str(count),
            "--output",
            str(output),
        ],
        timeout=900,
    )
    return output


async def _run_benchmark(
    *,
    suite_path: Path,
    output: Path,
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
        max_concurrency=4,
    )


def run(args: argparse.Namespace) -> int:
    env_values = load_simulation_values(args.env_file)
    llm = resolve_llm_settings(env_values)
    apply_compose_environment(llm)
    alert_webhook_secret = _setting(
        env_values,
        "REFERENCE_ALERT_WEBHOOK_SECRET",
        "reference-alert-webhook-secret-change-me-123456",
    )
    os.environ["REFERENCE_ALERT_WEBHOOK_SECRET"] = alert_webhook_secret
    os.environ["DEVOPS_AGENT_LOAD_WEBHOOK_SECRET"] = alert_webhook_secret
    _preflight_llm(llm.host_base_url, llm.model, llm.api_key)

    output_root = args.output_root.resolve()
    if output_root.exists():
        raise ValueError(f"simulation output already exists: {output_root}")
    output_root.mkdir(parents=True)
    stack_started = False
    try:
        _run(
            _compose_command(
                args.env_file,
                "up",
                "-d",
                *(('--build',) if args.build else ()),
            )
        )
        stack_started = True
        agent_port = _setting(env_values, "REFERENCE_AGENT_PORT", "28010")
        oidc_port = _setting(env_values, "REFERENCE_OIDC_PORT", "28453")
        mcp_port = _setting(env_values, "REFERENCE_MCP_PORT", "28092")
        ticketing_port = _setting(env_values, "SIM_TICKETING_PORT", "28083")
        prometheus_port = _setting(env_values, "REFERENCE_PROMETHEUS_PORT", "29190")
        loki_port = _setting(env_values, "REFERENCE_LOKI_PORT", "23110")
        tempo_port = _setting(env_values, "REFERENCE_TEMPO_PORT", "23210")
        _wait_http(f"http://localhost:{agent_port}/readyz")
        _wait_http(f"http://localhost:{ticketing_port}/healthz")

        _run(
            [
                sys.executable,
                str(PROJECT_ROOT / "ops/reference-staging/run_acceptance.py"),
                "--compose-file",
                str(REFERENCE_COMPOSE),
                "--compose-override",
                str(SIMULATION_COMPOSE),
                "--compose-project-name",
                PROJECT_NAME,
                "--compose-env-file",
                str(args.env_file),
                "--skip-start",
                "--simulation",
                "--environment",
                "production-like-simulation",
                "--agent-url",
                f"http://localhost:{agent_port}",
                "--oidc-token-url",
                f"https://localhost:{oidc_port}/token",
                "--provider-url",
                f"http://localhost:{ticketing_port}",
                "--prometheus-url",
                f"http://localhost:{prometheus_port}",
                "--loki-url",
                f"http://localhost:{loki_port}",
                "--tempo-url",
                f"http://localhost:{tempo_port}",
                "--mcp-url",
                f"http://localhost:{mcp_port}/mcp",
                "--output",
                str(output_root / "protocol-acceptance.json"),
            ]
        )

        _probe_ticketing(
            f"http://localhost:{ticketing_port}/api/tickets",
            output_root / "ticketing-acceptance.json",
        )
        suite_path = output_root / "simulation-runtime-inputs.json"
        suite_path.write_text(
            json.dumps(
                build_reference_suite(SCENARIO_ROOT, simulation=True),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        benchmark = asyncio.run(
            _run_benchmark(
                suite_path=suite_path,
                output=output_root / "benchmark",
                base_url=llm.host_base_url,
                api_key=llm.api_key,
                model=llm.model,
                provider=llm.provider,
                api_style=llm.api_style,
                input_cost=llm.input_cost_per_million,
                output_cost=llm.output_cost_per_million,
            )
        )

        load_path = _run_capacity_load(
            output_root=output_root,
            agent_url=f"http://localhost:{agent_port}",
            duration_seconds=args.load_duration_seconds,
        )
        _mark_load_simulation(load_path)

        completion_probe_path = _run_completion_probe(
            output_root=output_root,
            agent_url=f"http://localhost:{agent_port}",
            oidc_token_url=f"https://localhost:{oidc_port}/token",
            webhook_secret=alert_webhook_secret,
            count=args.completion_probe_count,
        )
        _attach_completion_probe(load_path, completion_probe_path)

        _run(
            [
                sys.executable,
                str(PROJECT_ROOT / "ops/chaos/run_reference_chaos.py"),
                "--compose-file",
                str(REFERENCE_COMPOSE),
                "--compose-override",
                str(SIMULATION_COMPOSE),
                "--compose-project-name",
                PROJECT_NAME,
                "--compose-env-file",
                str(args.env_file),
                "--agent-url",
                f"http://localhost:{agent_port}",
                "--oidc-token-url",
                f"https://localhost:{oidc_port}/token",
                "--simulation",
                "--output-directory",
                str(output_root / "chaos"),
            ]
        )
        summary = {
            "schema_version": "1.0",
            "simulation": True,
            "synthetic": False,
            "production_acceptance": False,
            "generated_at": datetime.now(UTC).isoformat(),
            "llm_provider": llm.provider,
            "llm_model": llm.model,
            "llm_api_style": llm.api_style,
            "llm_input_cost_per_million": llm.input_cost_per_million,
            "llm_output_cost_per_million": llm.output_cost_per_million,
            "llm_pricing_basis": llm.pricing_basis,
            "llm_max_retries": SIMULATION_MAX_RETRIES,
            "benchmark_total_runs": benchmark["summary"]["total_runs"],
            "benchmark_avg_estimated_cost_cny": benchmark["summary"]["avg_cost"],
            "artifacts": {
                "protocol": "protocol-acceptance.json",
                "ticketing": "ticketing-acceptance.json",
                "benchmark": "benchmark/results.json",
                "load": load_path.relative_to(output_root).as_posix(),
                "rca_completion_probe": completion_probe_path.relative_to(
                    output_root
                ).as_posix(),
                "chaos": "chaos/chaos-report.json",
            },
        }
        (output_root / "simulation-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0
    finally:
        if stack_started and not args.keep_stack:
            _run(
                _compose_command(
                    args.env_file,
                    "down",
                    "-v",
                    "--remove-orphans",
                )
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the production-like local AIOps simulation"
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--output-root", type=Path, default=_default_output_root())
    parser.add_argument(
        "--load-duration-seconds",
        type=float,
        default=300.0,
        help="每档容量负载持续时间；验收建议保持 300 秒",
    )
    parser.add_argument(
        "--completion-probe-count",
        type=int,
        default=3,
        help="仿真中真实创建并等待终态的 RCA workflow 数量",
    )
    parser.add_argument("--keep-stack", action="store_true")
    parser.add_argument(
        "--build",
        action="store_true",
        help="rebuild the agent image before starting; default reuses the local image",
    )
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
