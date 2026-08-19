from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx


def _metric_total(payload: str) -> float:
    total = 0.0
    for line in payload.splitlines():
        if not line.startswith("devops_agent_rca_total{"):
            continue
        try:
            total += float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
    return total


def _token(client: httpx.Client, endpoint: str) -> str:
    response = client.post(
        endpoint,
        data={
            "client_id": "reference-cli",
            "scope": "openid incidents:rca incidents:read rca:read",
        },
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("OIDC response did not contain an access token")
    return token


def _signed_alert(
    client: httpx.Client,
    *,
    agent_url: str,
    tenant_id: str,
    secret: str,
    index: int,
) -> str:
    event_id = f"simulation-rca-completion-{index}-{time.time_ns()}"
    body = json.dumps(
        {
            "tenant_id": tenant_id,
            "source": "production-like-simulation",
            "service_name": "checkout-service",
            "severity": "WARNING",
            "summary": "Production-like RCA completion probe",
            "starts_at": datetime.now(UTC).isoformat(),
            "fingerprint": event_id,
            "external_event_id": event_id,
        },
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    response = client.post(
        f"{agent_url.rstrip('/')}/api/v1/alerts",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-DevOps-Agent-Timestamp": timestamp,
            "X-DevOps-Agent-Signature": f"sha256={signature}",
        },
    )
    response.raise_for_status()
    data = response.json().get("data", {})
    incident_id = data.get("incident_id") if isinstance(data, dict) else None
    if not isinstance(incident_id, str) or not incident_id:
        raise RuntimeError("alert response did not contain incident_id")
    return incident_id


def _start_rca(
    client: httpx.Client,
    *,
    agent_url: str,
    tenant_id: str,
    incident_id: str,
    token: str,
    index: int,
) -> None:
    response = client.post(
        f"{agent_url.rstrip('/')}/api/v1/admin/tenants/{tenant_id}"
        f"/incidents/{incident_id}/rca",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": f"simulation-rca-completion-{index}",
        },
        json={},
    )
    response.raise_for_status()


def run_probe(
    *,
    agent_url: str,
    oidc_token_url: str,
    tenant_id: str,
    webhook_secret: str,
    count: int,
    timeout_seconds: float,
    output: Path,
) -> dict[str, object]:
    if not 1 <= count <= 20:
        raise ValueError("count must be between 1 and 20")
    if len(webhook_secret) < 32:
        raise ValueError("webhook_secret must contain at least 32 characters")
    started_at = datetime.now(UTC)
    with (
        httpx.Client(timeout=15, follow_redirects=False) as client,
        httpx.Client(timeout=15, follow_redirects=False, verify=False) as oidc,
    ):
        metrics_before = client.get(f"{agent_url.rstrip('/')}/metrics")
        metrics_before.raise_for_status()
        baseline = _metric_total(metrics_before.text)
        token = _token(oidc, oidc_token_url)
        observed_incident_ids = []
        for index in range(count):
            incident_id = _signed_alert(
                client,
                agent_url=agent_url,
                tenant_id=tenant_id,
                secret=webhook_secret,
                index=index,
            )
            observed_incident_ids.append(incident_id)

        # 告警风暴按服务、时间窗口和拓扑聚合到一个 Incident；RCA 是 Incident
        # 级别的 workflow，重复告警不能重复创建 active workflow。
        incident_ids = tuple(dict.fromkeys(observed_incident_ids))
        for index, incident_id in enumerate(incident_ids):
            _start_rca(
                client,
                agent_url=agent_url,
                tenant_id=tenant_id,
                incident_id=incident_id,
                token=token,
                index=index,
            )

        deadline = time.monotonic() + timeout_seconds
        completed = baseline
        while time.monotonic() < deadline:
            metrics_after = client.get(f"{agent_url.rstrip('/')}/metrics")
            metrics_after.raise_for_status()
            completed = _metric_total(metrics_after.text)
            if completed - baseline >= len(incident_ids):
                break
            time.sleep(1)
        metrics_after = client.get(f"{agent_url.rstrip('/')}/metrics")
        metrics_after.raise_for_status()
        completed_delta = max(0.0, _metric_total(metrics_after.text) - baseline)

    completion_rate = min(1.0, completed_delta / len(incident_ids))
    report = {
        "schema_version": "1.0",
        "simulation": True,
        "synthetic": False,
        "production_acceptance": False,
        "measurement_source": "platform-prometheus-metrics",
        "generated_at": datetime.now(UTC).isoformat(),
        "started_at": started_at.isoformat(),
        "requested_workflows": len(incident_ids),
        "completed_workflows": int(completed_delta),
        "completion_rate": round(completion_rate, 6),
        "timeout_seconds": timeout_seconds,
        "incident_ids_sha256": hashlib.sha256(
            "\n".join(incident_ids).encode()
        ).hexdigest(),
        "gate": {
            "passed": completion_rate >= 0.99,
            "checks": {
                "completion_rate_at_least_99_percent": completion_rate >= 0.99,
                "all_requested_workflows_terminal": completed_delta
                >= len(incident_ids),
            },
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real RCA completion probe")
    parser.add_argument("--agent-url", required=True)
    parser.add_argument("--oidc-token-url", required=True)
    parser.add_argument("--tenant-id", default="reference-tenant")
    parser.add_argument("--webhook-secret", required=True)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run_probe(
        agent_url=args.agent_url,
        oidc_token_url=args.oidc_token_url,
        tenant_id=args.tenant_id,
        webhook_secret=args.webhook_secret,
        count=args.count,
        timeout_seconds=args.timeout_seconds,
        output=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
