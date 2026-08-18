from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, time, time_ns
from typing import Any

import httpx

DEFAULT_RATES = (100, 500, 1000)


def build_local_load_report(
    rates: tuple[int, ...] = DEFAULT_RATES,
) -> dict[str, object]:
    """生成本地可重复负载计划；真实 HTTP/CPU 数据必须由目标环境采集。"""
    if not rates or any(rate <= 0 for rate in rates):
        raise ValueError("rates must contain positive values")
    profiles = []
    for rate in rates:
        profiles.append(
            {
                "alerts_per_minute": rate,
                "simulated": True,
                "http_p95_ms": None,
                "db_pool": None,
                "outbox_backlog": None,
                "kafka_lag": None,
                "rca_queue_delay_ms": None,
                "completion_rate": None,
                "cpu_percent": None,
                "memory_bytes": None,
                "known_limitation": "未连接真实目标环境，不填充性能数字。",
            }
        )
    return {
        "schema_version": "1.0",
        "mode": "contract",
        "simulated": True,
        "profiles": profiles,
    }


def build_k6_load_report(
    input_directory: Path,
    rates: tuple[int, ...] = DEFAULT_RATES,
    *,
    target_label: str = "configured-target",
) -> dict[str, object]:
    """汇总 k6 实测文件；缺失的基础设施快照保持 null。"""
    if not rates or any(rate <= 0 for rate in rates):
        raise ValueError("rates must contain positive values")
    profiles = []
    for rate in rates:
        k6_path = input_directory / f"load-{rate}.json"
        try:
            summary = json.loads(k6_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read k6 summary {k6_path}") from exc
        metrics = summary.get("metrics")
        if not isinstance(metrics, dict):
            raise ValueError(f"k6 summary has no metrics: {k6_path}")
        infra = _load_infrastructure_snapshot(input_directory, rate)
        profiles.append(
            {
                "alerts_per_minute": rate,
                "simulated": False,
                "measurement_source": "k6-summary-export",
                "http_p95_ms": _metric(metrics, "http_req_duration", "p(95)"),
                "error_rate": _metric(metrics, "http_req_failed", "rate"),
                "request_count": _metric(metrics, "http_reqs", "count"),
                "dropped_iterations": _metric(
                    metrics, "dropped_iterations", "count", default=0
                ),
                "check_rate": _metric(metrics, "checks", "rate"),
                "db_pool": infra.get("db_pool"),
                "outbox_backlog": infra.get("outbox_backlog"),
                "kafka_lag": infra.get("kafka_lag"),
                "rca_queue_delay_ms": infra.get("rca_queue_delay_ms"),
                "completion_rate": infra.get("completion_rate"),
                "cpu_percent": infra.get("cpu_percent"),
                "memory_bytes": infra.get("memory_bytes"),
                "infrastructure_snapshot_present": bool(infra),
            }
        )
    return {
        "schema_version": "1.0",
        "mode": "live",
        "simulated": False,
        "target_label": target_label,
        "generated_at": datetime.now(UTC).isoformat(),
        "profiles": profiles,
    }


def _metric(
    metrics: dict[str, Any],
    metric_name: str,
    value_name: str,
    *,
    default: int | float | None = None,
) -> int | float | None:
    metric = metrics.get(metric_name)
    if metric is None:
        return default
    if not isinstance(metric, dict) or not isinstance(metric.get("values"), dict):
        raise ValueError(f"k6 metric {metric_name} is invalid")
    value = metric["values"].get(value_name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"k6 metric {metric_name}.{value_name} is invalid")
    return value


def _load_infrastructure_snapshot(
    input_directory: Path,
    rate: int,
) -> dict[str, int | float | None]:
    path = input_directory / f"infra-{rate}.json"
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read infrastructure snapshot {path}") from exc
    allowed = {
        "db_pool",
        "outbox_backlog",
        "kafka_lag",
        "rca_queue_delay_ms",
        "completion_rate",
        "cpu_percent",
        "memory_bytes",
    }
    if not isinstance(document, dict) or not set(document).issubset(allowed):
        raise ValueError(f"infrastructure snapshot is invalid: {path}")
    for key, value in document.items():
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int | float) or value < 0
        ):
            raise ValueError(f"infrastructure metric {key} is invalid")
    return document


async def run_http_load_matrix(
    *,
    target_url: str,
    duration_seconds: float,
    rates: tuple[int, ...] = DEFAULT_RATES,
    tenant_id: str = "reference-tenant",
    synthetic: bool = True,
    settle_seconds: float = 2.0,
) -> dict[str, object]:
    """在缺少 k6 的参考环境按固定到达率采集真实 HTTP 延迟。"""
    if not 1 <= duration_seconds <= 3600:
        raise ValueError("duration_seconds must be between 1 and 3600")
    if not rates or any(rate <= 0 for rate in rates):
        raise ValueError("rates must contain positive values")
    base_url = target_url.rstrip("/")
    profiles = []
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
    async with httpx.AsyncClient(timeout=10, limits=limits) as client:
        for rate in rates:
            count = max(1, math.ceil(rate * duration_seconds / 60))
            started = perf_counter()
            results = await asyncio.gather(
                *(
                    _scheduled_alert(
                        client,
                        base_url,
                        tenant_id,
                        rate,
                        index,
                        delay=index * 60 / rate,
                    )
                    for index in range(count)
                )
            )
            remaining = duration_seconds - (perf_counter() - started)
            if remaining > 0:
                await asyncio.sleep(remaining)
            elapsed = perf_counter() - started
            await asyncio.sleep(settle_seconds)
            metrics_text = await _read_metrics(client, base_url)
            latencies = sorted(item[0] for item in results)
            failures = sum(1 for _, status in results if not 200 <= status < 300)
            profiles.append(
                {
                    "alerts_per_minute": rate,
                    "configured_duration_seconds": duration_seconds,
                    "actual_duration_seconds": round(elapsed, 6),
                    "actual_alerts_per_minute": round(count / elapsed * 60, 3),
                    "simulated": False,
                    "synthetic": synthetic,
                    "measurement_source": "python-reference-fallback",
                    "http_p95_ms": round(_percentile(latencies, 0.95), 3),
                    "error_rate": round(failures / count, 6),
                    "request_count": count,
                    "dropped_iterations": 0,
                    "check_rate": round((count - failures) / count, 6),
                    "db_pool": None,
                    "outbox_backlog": _prometheus_sum(
                        metrics_text, "devops_agent_outbox_backlog_events"
                    ),
                    "kafka_lag": _prometheus_value(
                        metrics_text,
                        'devops_agent_rca_consumer_lag_records{scope="total"}',
                    ),
                    "rca_queue_delay_ms": None,
                    "completion_rate": None,
                    "cpu_percent": None,
                    "memory_bytes": _prometheus_value(
                        metrics_text, "process_resident_memory_bytes"
                    ),
                    "known_limitations": [
                        "本机未安装 k6，使用等到达率 Python fallback。",
                        "DB pool、RCA queue delay、completion rate 和 CPU 未采集。",
                    ],
                }
            )
    return {
        "schema_version": "1.0",
        "mode": "http-live",
        "simulated": False,
        "synthetic": synthetic,
        "target_sha256": hashlib.sha256(base_url.encode()).hexdigest(),
        "generated_at": datetime.now(UTC).isoformat(),
        "profiles": profiles,
    }


async def _scheduled_alert(
    client: httpx.AsyncClient,
    base_url: str,
    tenant_id: str,
    rate: int,
    index: int,
    *,
    delay: float,
) -> tuple[float, int]:
    await asyncio.sleep(delay)
    identity = f"reference-load-{rate}-{index}-{time_ns()}"
    body = json.dumps(
        {
            "tenant_id": tenant_id,
            "source": "reference-load",
            "service_name": "synthetic-checkout",
            "severity": "WARNING",
            "summary": "Synthetic reference capacity observation",
            "starts_at": datetime.now(UTC).isoformat(),
            "fingerprint": identity,
            "external_event_id": identity,
        },
        separators=(",", ":"),
    ).encode()
    headers = {"Content-Type": "application/json"}
    secret = os.getenv("DEVOPS_AGENT_LOAD_WEBHOOK_SECRET")
    if secret:
        timestamp = str(int(time()))
        signature = hmac.new(
            secret.encode(),
            timestamp.encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        headers.update(
            {
                "X-DevOps-Agent-Timestamp": timestamp,
                "X-DevOps-Agent-Signature": f"sha256={signature}",
            }
        )
    started = perf_counter()
    try:
        response = await client.post(
            f"{base_url}/api/v1/alerts", content=body, headers=headers
        )
        status = response.status_code
    except httpx.HTTPError:
        status = 0
    return (perf_counter() - started) * 1000, status


async def _read_metrics(client: httpx.AsyncClient, base_url: str) -> str:
    try:
        response = await client.get(f"{base_url}/metrics")
        response.raise_for_status()
    except httpx.HTTPError:
        return ""
    return response.text


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = max(0, math.ceil(len(values) * percentile) - 1)
    return values[index]


def _prometheus_value(text: str, metric: str) -> float | None:
    prefix = metric + " "
    for line in text.splitlines():
        if line.startswith(prefix):
            try:
                return float(line[len(prefix) :])
            except ValueError:
                return None
    return None


def _prometheus_sum(text: str, metric: str) -> float | None:
    values = []
    for line in text.splitlines():
        if not line.startswith(metric + "{"):
            continue
        try:
            values.append(float(line.rsplit(" ", 1)[1]))
        except (IndexError, ValueError):
            return None
    return sum(values) if values else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DevOps Agent 负载报告")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("contract", "live", "http-live"),
        default="contract",
    )
    parser.add_argument("--input-directory", type=Path)
    parser.add_argument("--target-label", default="configured-target")
    parser.add_argument("--target-url")
    parser.add_argument("--duration-seconds", type=float, default=6)
    parser.add_argument("--tenant-id", default="reference-tenant")
    parser.add_argument("--non-synthetic", action="store_true")
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("load report already exists; choose a new output path")
    if args.mode == "live":
        if args.input_directory is None:
            raise ValueError("--input-directory is required in live mode")
        report = build_k6_load_report(
            args.input_directory,
            target_label=args.target_label,
        )
    elif args.mode == "http-live":
        if args.target_url is None:
            raise ValueError("--target-url is required in http-live mode")
        report = asyncio.run(
            run_http_load_matrix(
                target_url=args.target_url,
                duration_seconds=args.duration_seconds,
                tenant_id=args.tenant_id,
                synthetic=not args.non_synthetic,
            )
        )
    else:
        report = build_local_load_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
