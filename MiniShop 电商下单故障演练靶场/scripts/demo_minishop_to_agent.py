import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.send_agent_alert import (  # noqa: E402
    build_alert_payload,
    post_json as post_agent_alert,
)


def post_minishop(base_url: str, path: str, payload: dict | None = None) -> tuple[int, str]:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json", "x-trace-id": f"demo_{uuid4().hex[:12]}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inject a MiniShop fault and send a compatible alert to DevOps Agent."
    )
    parser.add_argument("--minishop-url", default="http://127.0.0.1:18080")
    parser.add_argument("--agent-url", default="http://127.0.0.1:8000/api/v1/alerts")
    parser.add_argument("--webhook-secret", default="")
    parser.add_argument("--skip-agent-send", action="store_true")
    args = parser.parse_args()

    print("1. Inject inventory db timeout fault into MiniShop")
    fault_status, fault_body = post_minishop(
        args.minishop_url,
        "/faults/inventory-db-timeout",
        {"delay_ms": 1200, "duration_seconds": 300, "created_by": "agent-link-demo"},
    )
    print(f"   MiniShop fault response: HTTP {fault_status} {fault_body}")

    print("2. Trigger checkout so the fault becomes visible")
    checkout_status, checkout_body = post_minishop(
        args.minishop_url,
        "/checkout",
        {"user_id": "demo-user", "items": [{"sku": "sku-001", "quantity": 1}]},
    )
    print(f"   MiniShop checkout response: HTTP {checkout_status} {checkout_body}")

    external_event_id = f"minishop-inventory-db-timeout-{int(time.time())}"
    payload = build_alert_payload(
        service_name="inventory-service",
        severity="CRITICAL",
        summary="MiniShop inventory-service database timeout caused checkout failure",
        external_event_id=external_event_id,
        fingerprint="minishop:inventory-service:db-timeout",
    )
    print("3. DevOps Agent alert payload")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.skip_agent_send:
        print("4. Skipped sending to DevOps Agent")
        return

    print(f"4. Send alert to DevOps Agent: {args.agent_url}")
    try:
        status = post_agent_alert(
            args.agent_url,
            payload,
            webhook_secret=args.webhook_secret,
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"   DevOps Agent rejected alert: HTTP {exc.code} {body}")
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        print(f"   DevOps Agent is not reachable: {exc}")
        raise SystemExit(1) from exc
    print(f"   DevOps Agent response status: HTTP {status}")


if __name__ == "__main__":
    main()
