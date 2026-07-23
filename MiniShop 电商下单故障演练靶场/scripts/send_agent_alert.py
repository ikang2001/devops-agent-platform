import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone

from app.agent_alerts import build_signed_headers


def build_alert_payload(
    *,
    service_name: str = "checkout-service",
    severity: str = "CRITICAL",
    summary: str = "checkout-service p95 latency is higher than 1s",
    external_event_id: str = "checkout-high-latency-001",
    fingerprint: str = "minishop:checkout-service:high-latency",
) -> dict:
    return {
        "tenant_id": "demo",
        "source": "alertmanager",
        "service_name": service_name,
        "severity": severity,
        "summary": summary,
        "starts_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fingerprint,
        "external_event_id": external_event_id,
    }


def post_json(url: str, payload: dict, *, webhook_secret: str = "") -> int:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if webhook_secret:
        headers.update(build_signed_headers(data, webhook_secret))
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
        return response.status


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or send a DevOps Agent alert payload.")
    parser.add_argument("--agent-url", default="")
    parser.add_argument("--service-name", default="checkout-service")
    parser.add_argument("--severity", default="CRITICAL")
    parser.add_argument("--summary", default="checkout-service p95 latency is higher than 1s")
    parser.add_argument("--external-event-id", default="checkout-high-latency-001")
    parser.add_argument("--fingerprint", default="minishop:checkout-service:high-latency")
    parser.add_argument(
        "--webhook-secret",
        default=os.getenv("DEVOPS_AGENT_ALERT_WEBHOOK_SECRET", ""),
        help="Optional HMAC secret. Defaults to DEVOPS_AGENT_ALERT_WEBHOOK_SECRET.",
    )
    args = parser.parse_args()

    payload = build_alert_payload(
        service_name=args.service_name,
        severity=args.severity,
        summary=args.summary,
        external_event_id=args.external_event_id,
        fingerprint=args.fingerprint,
    )

    if not args.agent_url:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    status = post_json(args.agent_url, payload, webhook_secret=args.webhook_secret)
    print(f"sent alert to {args.agent_url}, status={status}")


if __name__ == "__main__":
    main()
