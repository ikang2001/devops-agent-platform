from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from uuid import uuid4

import httpx


def run_acceptance(
    *,
    compose_file: Path | tuple[Path, ...],
    output: Path,
    start_stack: bool,
    compose_project_name: str | None = None,
    compose_env_file: Path | None = None,
    agent_url: str = "http://localhost:28000",
    oidc_token_url: str = "https://localhost:28443/token",
    prometheus_url: str = "http://localhost:29090",
    loki_url: str = "http://localhost:23100",
    tempo_url: str = "http://localhost:23200",
    provider_url: str = "http://localhost:28081",
    mcp_url: str = "http://localhost:28082/mcp",
    environment: str = "reference-staging",
    simulation: bool = False,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("acceptance output already exists; choose a new evidence path")
    if start_stack:
        _compose(
            compose_file,
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "240",
            project_name=compose_project_name,
            env_file=compose_env_file,
        )
    checks: list[dict[str, Any]] = []
    with (
        httpx.Client(timeout=10, follow_redirects=False) as client,
        httpx.Client(timeout=10, follow_redirects=False, verify=False) as oidc_client,
    ):
        token = _check(
            checks,
            "oidc-token",
            lambda: _oidc_token(oidc_client, endpoint=oidc_token_url),
        )
        _check(
            checks,
            "agent-readiness",
            lambda: _expect_status(client, f"{agent_url.rstrip('/')}/readyz"),
        )
        if isinstance(token, str):
            _check(
                checks,
                "workspace-oidc-api",
                lambda: _workspace_round_trip(client, token, agent_url=agent_url),
            )
            _check(
                checks,
                "dataset-release-api",
                lambda: _dataset_release_round_trip(
                    client,
                    oidc_client,
                    token,
                    agent_url=agent_url,
                    oidc_token_url=oidc_token_url,
                    synthetic=not simulation,
                ),
            )
        else:
            checks.append(
                {
                    "name": "workspace-oidc-api",
                    "passed": False,
                    "latency_ms": None,
                    "error": "OIDC token check failed",
                }
            )
        for name, url in (
            ("prometheus", f"{prometheus_url.rstrip('/')}/-/ready"),
            ("loki", f"{loki_url.rstrip('/')}/ready"),
            ("tempo", f"{tempo_url.rstrip('/')}/ready"),
            ("llm-ticketing-provider", f"{provider_url.rstrip('/')}/healthz"),
        ):
            _check(checks, name, lambda url=url: _wait_status(client, url))
        _check(checks, "mcp-jsonrpc", lambda: _mcp_call(client, mcp_url=mcp_url))
    _check(
        checks,
        "postgresql",
        lambda: _compose(
            compose_file,
            "exec",
            "-T",
            "postgres",
            "pg_isready",
            project_name=compose_project_name,
            env_file=compose_env_file,
        ),
    )
    _check(
        checks,
        "kafka-redpanda",
        lambda: _compose(
            compose_file,
            "exec",
            "-T",
            "redpanda",
            "rpk",
            "cluster",
            "health",
            project_name=compose_project_name,
            env_file=compose_env_file,
        ),
    )
    limitations = (
        [
            "LLM 和 Ticketing 使用本地仿真服务；报告不代表外部供应商签字。",
            "仿真报告只证明协议、装配和恢复流程，不代表生产容量、成本或恢复时间。",
        ]
        if simulation
        else [
            "OIDC、LLM、Ticketing 和 MCP 是本地 reference provider。",
            "该报告只能证明协议接线，不能作为生产容量、成本或恢复时间。",
        ]
    )
    report = {
        "schema_version": "1.0",
        "environment": environment,
        "synthetic": not simulation,
        "simulation": simulation,
        "production_acceptance": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "limitations": limitations,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _check(
    checks: list[dict[str, Any]],
    name: str,
    operation: Callable[[], Any],
) -> Any:
    started = perf_counter()
    try:
        result = operation()
    except (OSError, RuntimeError, httpx.HTTPError) as exc:
        checks.append(
            {
                "name": name,
                "passed": False,
                "latency_ms": round((perf_counter() - started) * 1000, 3),
                "error": str(exc)[:1024],
            }
        )
        return None
    checks.append(
        {
            "name": name,
            "passed": True,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
            "error": None,
        }
    )
    return result


def _oidc_token(
    client: httpx.Client,
    *,
    endpoint: str = "https://localhost:28443/token",
    client_id: str = "reference-cli",
    scopes: str = (
        "openid workspaces:read workspaces:write datasets:curate datasets:review"
    ),
) -> str:
    response = client.post(
        endpoint,
        data={
            "client_id": client_id,
            "scope": scopes,
        },
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("OIDC token response is invalid")
    return token


def _workspace_round_trip(
    client: httpx.Client,
    token: str,
    *,
    agent_url: str = "http://localhost:28000",
) -> None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "reference-workspace-v1",
        "If-Match": '"0"',
        "X-Trace-Id": "reference-workspace-acceptance",
    }
    payload = {
        "name": "Reference Workspace",
        "prometheus_target": "http://prometheus:9090",
        "loki_target": "http://loki:3100",
        "tempo_target": "http://tempo:3200",
        "knowledge_scope": "tenant",
        "investigation_policy": "fixed_default",
        "allowed_tools": ["metrics.query@v1", "logs.query@v1"],
        "llm_provider_policy": "reference-synthetic",
        "retention_days": 7,
    }
    put = client.put(
        f"{agent_url.rstrip('/')}/api/v1/admin/tenants/reference-tenant/workspaces/reference",
        headers=headers,
        json=payload,
    )
    put.raise_for_status()
    get = client.get(
        f"{agent_url.rstrip('/')}/api/v1/admin/tenants/reference-tenant/workspaces/reference",
        headers={"Authorization": f"Bearer {token}"},
    )
    get.raise_for_status()
    if get.json().get("data", {}).get("revision") != 1:
        raise RuntimeError("workspace API returned an unexpected revision")


def _dataset_release_round_trip(
    client: httpx.Client,
    oidc_client: httpx.Client,
    curator_token: str,
    *,
    agent_url: str = "http://localhost:28000",
    oidc_token_url: str = "https://localhost:28443/token",
    synthetic: bool = True,
) -> None:
    """验证创建、双审核、发布、幂等和发布后不可变。"""
    privacy_token = _oidc_token(
        oidc_client,
        endpoint=oidc_token_url,
        client_id="reference-cli-privacy",
        scopes="openid datasets:review datasets:read",
    )
    domain_token = _oidc_token(
        oidc_client,
        endpoint=oidc_token_url,
        client_id="reference-cli-domain",
        scopes="openid datasets:review datasets:read",
    )
    publish_token = _oidc_token(
        oidc_client,
        endpoint=oidc_token_url,
        scopes="openid datasets:publish datasets:read",
    )
    release_id = f"reference-release-{uuid4().hex[:12]}"
    dataset_id = f"minishop-v2-reference-{uuid4().hex[:8]}"
    url = (
        f"{agent_url.rstrip('/')}/api/v1/admin/tenants/"
        f"reference-tenant/dataset-releases/{release_id}"
    )
    payload = {
        "dataset_id": dataset_id,
        "source_version": "v1",
        "version": "v2",
        "dataset": {
            "dataset_id": dataset_id,
            "version": "v2",
            "privacy": "anonymized",
            "samples": [{"scenario_id": "reference-synthetic"}],
        },
        "candidate_sha256": "a" * 64,
        "curation_review_sha256": "b" * 64,
        "synthetic": synthetic,
    }
    create_headers = {
        "Authorization": f"Bearer {curator_token}",
        "Idempotency-Key": f"{release_id}-create",
        "X-Trace-Id": f"{release_id}-create-trace",
    }
    create = client.put(url, headers=create_headers, json=payload)
    create.raise_for_status()
    if create.json().get("data", {}).get("revision") != 1:
        raise RuntimeError("dataset release create returned an unexpected revision")
    duplicate = client.put(url, headers=create_headers, json=payload)
    duplicate.raise_for_status()
    if duplicate.json().get("data", {}).get("release_id") != release_id:
        raise RuntimeError("dataset release idempotency returned a different release")

    review_base = {
        "Authorization": f"Bearer {domain_token}",
        "If-Match": '"1"',
        "Idempotency-Key": f"{release_id}-domain-review",
    }
    domain_review = client.post(
        f"{url}/reviews",
        headers=review_base,
        json={"role": "domain", "approved": True, "notes": "Reference domain review."},
    )
    domain_review.raise_for_status()
    privacy_review = client.post(
        f"{url}/reviews",
        headers={
            "Authorization": f"Bearer {privacy_token}",
            "If-Match": '"2"',
            "Idempotency-Key": f"{release_id}-privacy-review",
        },
        json={
            "role": "privacy",
            "approved": True,
            "notes": "Reference privacy review.",
        },
    )
    privacy_review.raise_for_status()
    publish = client.post(
        f"{url}/publish",
        headers={
            "Authorization": f"Bearer {publish_token}",
            "If-Match": '"3"',
            "Idempotency-Key": f"{release_id}-publish",
        },
    )
    publish.raise_for_status()
    published = client.get(
        url,
        headers={"Authorization": f"Bearer {publish_token}"},
    )
    published.raise_for_status()
    release = published.json().get("data", {})
    if release.get("status") != "PUBLISHED" or release.get("revision") != 4:
        raise RuntimeError("dataset release did not reach immutable PUBLISHED state")
    immutable = client.post(
        f"{url}/reviews",
        headers={
            "Authorization": f"Bearer {curator_token}",
            "If-Match": '"4"',
            "Idempotency-Key": f"{release_id}-immutable-check",
        },
        json={"role": "domain", "approved": True, "notes": "Must be rejected."},
    )
    if immutable.status_code != 409:
        raise RuntimeError("published dataset release accepted a mutable review")


def _mcp_call(
    client: httpx.Client,
    *,
    mcp_url: str = "http://localhost:28082/mcp",
) -> None:
    response = client.post(
        mcp_url,
        headers={
            "Authorization": "Bearer reference-mcp-token-change-me",
            "MCP-Protocol-Version": "2025-06-18",
        },
        json={
            "jsonrpc": "2.0",
            "id": "reference-acceptance",
            "method": "tools/call",
            "params": {
                "name": "reference.observability.lookup",
                "arguments": {"key": "checkout-api"},
            },
        },
    )
    response.raise_for_status()
    document = response.json()
    if document.get("result", {}).get("structuredContent", {}).get("found") is not True:
        raise RuntimeError("MCP reference tool returned an invalid result")


def _expect_status(client: httpx.Client, url: str) -> None:
    response = client.get(url)
    response.raise_for_status()


def _wait_status(client: httpx.Client, url: str, attempts: int = 60) -> None:
    last_error: httpx.HTTPError | None = None
    for _ in range(attempts):
        try:
            _expect_status(client, url)
            return
        except httpx.HTTPError as exc:
            last_error = exc
            sleep(1)
    raise RuntimeError(f"readiness did not converge: {last_error}")


def _compose(
    compose_file: Path | tuple[Path, ...],
    *arguments: str,
    project_name: str | None = None,
    env_file: Path | None = None,
) -> None:
    compose_files = (compose_file,) if isinstance(compose_file, Path) else compose_file
    command = ["docker", "compose"]
    if project_name:
        command.extend(("--project-name", project_name))
    if env_file:
        command.extend(("--env-file", str(env_file)))
    for path in compose_files:
        command.extend(("-f", str(path)))
    completed = subprocess.run(
        [*command, *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(message[-2048:] or "docker compose command failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run reference staging acceptance")
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=Path(__file__).with_name("docker-compose.yml"),
    )
    parser.add_argument("--compose-override", type=Path, action="append", default=[])
    parser.add_argument("--compose-project-name")
    parser.add_argument("--compose-env-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-start", action="store_true")
    parser.add_argument("--agent-url", default="http://localhost:28000")
    parser.add_argument("--oidc-token-url", default="https://localhost:28443/token")
    parser.add_argument("--prometheus-url", default="http://localhost:29090")
    parser.add_argument("--loki-url", default="http://localhost:23100")
    parser.add_argument("--tempo-url", default="http://localhost:23200")
    parser.add_argument("--provider-url", default="http://localhost:28081")
    parser.add_argument("--mcp-url", default="http://localhost:28082/mcp")
    parser.add_argument("--environment", default="reference-staging")
    parser.add_argument("--simulation", action="store_true")
    args = parser.parse_args(argv)
    try:
        compose_files = (
            (args.compose_file, *args.compose_override)
            if args.compose_override
            else args.compose_file
        )
        report = run_acceptance(
            compose_file=compose_files,
            output=args.output,
            start_stack=not args.skip_start,
            compose_project_name=args.compose_project_name,
            compose_env_file=args.compose_env_file,
            agent_url=args.agent_url,
            oidc_token_url=args.oidc_token_url,
            prometheus_url=args.prometheus_url,
            loki_url=args.loki_url,
            tempo_url=args.tempo_url,
            provider_url=args.provider_url,
            mcp_url=args.mcp_url,
            environment=args.environment,
            simulation=args.simulation,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
