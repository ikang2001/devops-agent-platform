import argparse
import json
import time
import urllib.request
from uuid import uuid4


def post_json(url: str, payload: dict) -> int:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "x-trace-id": f"load_{uuid4().hex[:12]}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code


def main() -> None:
    parser = argparse.ArgumentParser(description="Send repeated checkout requests to MiniShop.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()

    for index in range(args.count):
        payload = {
            "user_id": "load-user",
            "items": [{"sku": "sku-001", "quantity": 1}],
            "idempotency_key": f"load-{index}-{uuid4().hex[:8]}",
        }
        status = post_json(f"{args.base_url}/checkout", payload)
        print(f"{index + 1}/{args.count} status={status}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
