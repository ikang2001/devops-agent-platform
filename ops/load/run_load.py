from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, time, time_ns
from typing import Any

import httpx

DEFAULT_RATES = (100, 500, 1000)
TARGET_RATE_TOLERANCE = 0.05
MAX_ERROR_RATE = 0.01
MAX_P95_MS = 500.0
MAX_P99_MS = 1000.0
MIN_COMPLETION_RATE = 0.99


@dataclass(frozen=True)
class _AlertResult:
    latency_ms: float
    status_code: int | None
    outcome: str
    started_at: float


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
    duration_seconds: float | None = None,
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
        infra = _load_infrastructure_snapshot(
            input_directory,
            rate,
            duration_seconds=duration_seconds,
        )
        target_duration = duration_seconds or infra.get("duration_seconds")
        alert_count = _metric(
            metrics,
            "alert_requests",
            "count",
            default=_metric(metrics, "http_reqs", "count"),
        )
        achieved_rate = (
            float(alert_count) / float(target_duration) * 60
            if alert_count is not None and target_duration
            else None
        )
        status_distribution = _load_status_distribution(input_directory, rate)
        if not status_distribution:
            status_distribution = {
                name: int(value)
                for name, metric_name in (
                    ("2xx", "alert_accepted"),
                    ("4xx", "alert_4xx"),
                    ("5xx", "alert_5xx"),
                    ("timeout", "alert_timeouts"),
                )
                if (value := _metric(metrics, metric_name, "count")) is not None
            }
        profiles.append(
            {
                "alerts_per_minute": rate,
                "offered_alerts_per_minute": rate,
                "achieved_alerts_per_minute": (
                    round(achieved_rate, 3) if achieved_rate is not None else None
                ),
                "arrival_rate_error_ratio": (
                    round(abs(achieved_rate - rate) / rate, 6)
                    if achieved_rate is not None
                    else None
                ),
                "configured_duration_seconds": target_duration,
                "simulated": False,
                "measurement_source": "k6-summary-export",
                "http_p95_ms": _metric(
                    metrics,
                    "alert_http_duration",
                    "p(95)",
                    default=_metric(metrics, "http_req_duration", "p(95)"),
                ),
                "http_p99_ms": _metric(
                    metrics,
                    "alert_http_duration",
                    "p(99)",
                    default=_metric(metrics, "http_req_duration", "p(99)"),
                ),
                "error_rate": _metric(
                    metrics,
                    "alert_failed",
                    "rate",
                    default=_metric(metrics, "http_req_failed", "rate"),
                ),
                "request_count": alert_count,
                "dropped_iterations": _metric(
                    metrics, "dropped_iterations", "count", default=0
                ),
                "check_rate": (
                    1
                    - float(
                        _metric(
                            metrics,
                            "alert_failed",
                            "rate",
                            default=_metric(metrics, "http_req_failed", "rate"),
                        )
                        or 0
                    )
                ),
                "status_code_distribution": status_distribution,
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
        profiles[-1]["ingestion_gate"] = evaluate_ingestion_gate(profiles[-1])
        profiles[-1]["end_to_end_gate"] = evaluate_end_to_end_gate(profiles[-1])
    return {
        "schema_version": "1.0",
        "mode": "live",
        "simulated": False,
        "target_label": target_label,
        "generated_at": datetime.now(UTC).isoformat(),
        "profiles": profiles,
    }


def _load_status_distribution(
    input_directory: Path,
    rate: int,
) -> dict[str, int]:
    path = input_directory / f"status-{rate}.json"
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read k6 status summary {path}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"k6 status summary is invalid: {path}")
    result: dict[str, int] = {}
    for key, value in document.items():
        if (
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ValueError(f"k6 status summary is invalid: {path}")
        result[key] = value
    return result


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


def evaluate_ingestion_gate(profile: dict[str, object]) -> dict[str, object]:
    """按容量验收规范评估入口到达率、错误率、丢弃和延迟。"""

    rate = profile.get("alerts_per_minute")
    achieved = profile.get("achieved_alerts_per_minute")
    arrival_ok = (
        isinstance(rate, int | float)
        and isinstance(achieved, int | float)
        and abs(float(achieved) - float(rate)) / float(rate)
        <= TARGET_RATE_TOLERANCE
    )
    error_rate = profile.get("error_rate")
    error_ok = (
        isinstance(error_rate, int | float)
        and float(error_rate) < MAX_ERROR_RATE
    )
    dropped = profile.get("dropped_iterations")
    dropped_ok = isinstance(dropped, int | float) and float(dropped) == 0
    p95 = profile.get("http_p95_ms")
    p99 = profile.get("http_p99_ms")
    latency_ok = (
        isinstance(p95, int | float)
        and isinstance(p99, int | float)
        and float(p95) < MAX_P95_MS
        and float(p99) < MAX_P99_MS
    )
    duration = profile.get("configured_duration_seconds")
    duration_ok = isinstance(duration, int | float) and float(duration) >= 60
    checks = {
        "duration_seconds_at_least_60": duration_ok,
        "arrival_rate_within_5_percent": arrival_ok,
        "error_rate_below_1_percent": error_ok,
        "dropped_iterations_zero": dropped_ok,
        "p95_below_500_ms_and_p99_below_1000_ms": latency_ok,
    }
    return {"passed": all(checks.values()), "checks": checks}


def evaluate_end_to_end_gate(profile: dict[str, object]) -> dict[str, object]:
    """在入口门禁之上检查 RCA 完成和异步积压是否可观测。"""

    ingestion = evaluate_ingestion_gate(profile)
    completion = profile.get("completion_rate")
    completion_ok = (
        isinstance(completion, int | float)
        and float(completion) >= MIN_COMPLETION_RATE
    )
    outbox = profile.get("outbox_backlog")
    outbox_ok = isinstance(outbox, int | float) and float(outbox) == 0
    kafka_lag = profile.get("kafka_lag")
    kafka_ok = isinstance(kafka_lag, int | float) and float(kafka_lag) == 0
    checks = dict(ingestion["checks"])
    checks.update(
        {
            "rca_completion_rate_at_least_99_percent": completion_ok,
            "outbox_backlog_recovered": outbox_ok,
            "kafka_lag_recovered": kafka_ok,
        }
    )
    return {"passed": all(checks.values()), "checks": checks}


def _load_infrastructure_snapshot(
    input_directory: Path,
    rate: int,
    *,
    duration_seconds: float | None = None,
) -> dict[str, int | float | None]:
    path = input_directory / f"infra-{rate}.json"
    if not path.exists():
        return _load_prometheus_snapshot(
            input_directory,
            rate,
            duration_seconds=duration_seconds,
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read infrastructure snapshot {path}") from exc
    allowed = {
        "duration_seconds",
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


def _load_prometheus_snapshot(
    input_directory: Path,
    rate: int,
    *,
    duration_seconds: float | None,
) -> dict[str, int | float | None]:
    before_path = input_directory / f"metrics-{rate}-before.prom"
    after_path = input_directory / f"metrics-{rate}-after.prom"
    if not before_path.exists() or not after_path.exists():
        return {}
    try:
        before = before_path.read_text(encoding="utf-8-sig")
        after = after_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"could not read Prometheus snapshots for {rate}") from exc
    duration = float(duration_seconds) if duration_seconds else None
    expected = math.ceil(rate * duration / 60) if duration else None
    rca_before = _prometheus_sum(before, "devops_agent_rca_total")
    rca_after = _prometheus_sum(after, "devops_agent_rca_total")
    completion = (
        max(0.0, min(1.0, (rca_after - rca_before) / expected))
        if expected and rca_before is not None and rca_after is not None
        else None
    )
    cpu_before = _prometheus_value(before, "process_cpu_seconds_total")
    cpu_after = _prometheus_value(after, "process_cpu_seconds_total")
    cpu_percent = (
        max(0.0, (cpu_after - cpu_before) / duration * 100)
        if duration and cpu_before is not None and cpu_after is not None
        else None
    )
    return {
        "duration_seconds": duration,
        "db_pool": None,
        "outbox_backlog": _prometheus_sum(
            after,
            "devops_agent_outbox_backlog_events",
        ),
        "kafka_lag": _prometheus_value(
            after,
            'devops_agent_rca_consumer_lag_records{scope="total"}',
        ),
        "rca_queue_delay_ms": None,
        "completion_rate": completion,
        "cpu_percent": cpu_percent,
        "memory_bytes": _prometheus_value(after, "process_resident_memory_bytes"),
    }


async def run_http_load_matrix(
    *,
    target_url: str,
    duration_seconds: float,
    rates: tuple[int, ...] = DEFAULT_RATES,
    tenant_id: str = "reference-tenant",
    synthetic: bool = True,
    settle_seconds: float = 10.0,
    webhook_secret: str | None = None,
    max_workers: int = 200,
) -> dict[str, object]:
    """在缺少 k6 的参考环境按固定到达率采集真实 HTTP 延迟。"""
    if not 1 <= duration_seconds <= 3600:
        raise ValueError("duration_seconds must be between 1 and 3600")
    if not rates or any(rate <= 0 for rate in rates):
        raise ValueError("rates must contain positive values")
    if not 1 <= max_workers <= 1000:
        raise ValueError("max_workers must be between 1 and 1000")
    secret = webhook_secret or os.getenv("DEVOPS_AGENT_LOAD_WEBHOOK_SECRET")
    if not secret or len(secret) < 32 or secret != secret.strip():
        raise ValueError(
            "DEVOPS_AGENT_LOAD_WEBHOOK_SECRET must contain at least 32 bytes"
        )
    base_url = target_url.rstrip("/")
    profiles = []
    limits = httpx.Limits(
        max_connections=max_workers,
        max_keepalive_connections=min(max_workers, 100),
    )
    async with httpx.AsyncClient(timeout=10, limits=limits) as client:
        for rate in rates:
            count = max(1, math.ceil(rate * duration_seconds / 60))
            metrics_before = await _read_metrics(client, base_url)
            results, dropped, dispatch_window, elapsed = await _run_rate(
                client=client,
                base_url=base_url,
                tenant_id=tenant_id,
                rate=rate,
                count=count,
                duration_seconds=duration_seconds,
                webhook_secret=secret,
                max_workers=max_workers,
            )
            await asyncio.sleep(settle_seconds)
            metrics_after = await _read_metrics(client, base_url)
            latencies = sorted(item.latency_ms for item in results)
            failures = sum(
                1
                for item in results
                if item.status_code is None
                or not 200 <= item.status_code < 300
            )
            metrics_text = metrics_after
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
            profiles[-1].update(
                _build_python_profile(
                    rate=rate,
                    duration_seconds=duration_seconds,
                    elapsed=elapsed,
                    dispatch_window=dispatch_window,
                    results=results,
                    dropped=dropped,
                    metrics_before=metrics_before,
                    metrics_after=metrics_after,
                    synthetic=synthetic,
                )
            )
            profiles[-1]["ingestion_gate"] = evaluate_ingestion_gate(profiles[-1])
            profiles[-1]["end_to_end_gate"] = evaluate_end_to_end_gate(profiles[-1])
    return {
        "schema_version": "1.0",
        "mode": "http-live",
        "simulated": False,
        "synthetic": synthetic,
        "target_sha256": hashlib.sha256(base_url.encode()).hexdigest(),
        "generated_at": datetime.now(UTC).isoformat(),
        "profiles": profiles,
    }


async def _run_rate(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    tenant_id: str,
    rate: int,
    count: int,
    duration_seconds: float,
    webhook_secret: str,
    max_workers: int,
) -> tuple[list[_AlertResult], int, float, float]:
    interval = 60 / rate
    queue: asyncio.Queue[tuple[int, float] | None] = asyncio.Queue(
        maxsize=max(1, max_workers * 2)
    )
    results: list[_AlertResult] = []
    dropped = 0
    started = perf_counter()
    results_lock = asyncio.Lock()

    async def producer() -> None:
        nonlocal dropped
        for index in range(count):
            scheduled_at = started + index * interval
            remaining = scheduled_at - perf_counter()
            if remaining > 0:
                await asyncio.sleep(remaining)
            try:
                queue.put_nowait((index, scheduled_at))
            except asyncio.QueueFull:
                dropped += 1

    async def worker() -> None:
        while True:
            job = await queue.get()
            try:
                if job is None:
                    return
                index, scheduled_at = job
                result = await _scheduled_alert(
                    client,
                    base_url,
                    tenant_id,
                    rate,
                    index,
                    scheduled_at=scheduled_at,
                    webhook_secret=webhook_secret,
                )
                async with results_lock:
                    results.append(result)
            finally:
                queue.task_done()

    worker_count = min(max_workers, max(4, math.ceil(rate / 60 * 2)))
    workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
    await producer()
    await queue.join()
    for _ in workers:
        await queue.put(None)
    await asyncio.gather(*workers)
    elapsed = perf_counter() - started
    started_times = sorted(item.started_at for item in results)
    dispatch_window = (
        max(duration_seconds, started_times[-1] - started + interval)
        if started_times
        else duration_seconds
    )
    return results, dropped, dispatch_window, elapsed


def _build_python_profile(
    *,
    rate: int,
    duration_seconds: float,
    elapsed: float,
    dispatch_window: float,
    results: list[_AlertResult],
    dropped: int,
    metrics_before: str,
    metrics_after: str,
    synthetic: bool,
) -> dict[str, object]:
    latencies = sorted(item.latency_ms for item in results)
    planned = max(1, math.ceil(rate * duration_seconds / 60))
    failures = sum(
        1
        for item in results
        if item.status_code is None or not 200 <= item.status_code < 300
    )
    status_distribution = Counter(item.outcome for item in results)
    status_distribution["dropped"] = dropped
    outbox_before = _prometheus_sum(
        metrics_before, "devops_agent_outbox_backlog_events"
    )
    outbox_after = _prometheus_sum(
        metrics_after, "devops_agent_outbox_backlog_events"
    )
    kafka_before = _prometheus_value(
        metrics_before,
        'devops_agent_rca_consumer_lag_records{scope="total"}',
    )
    kafka_after = _prometheus_value(
        metrics_after,
        'devops_agent_rca_consumer_lag_records{scope="total"}',
    )
    rca_before = _prometheus_sum(metrics_before, "devops_agent_rca_total")
    rca_after = _prometheus_sum(metrics_after, "devops_agent_rca_total")
    completion = (
        max(0.0, min(1.0, (rca_after - rca_before) / planned))
        if rca_before is not None and rca_after is not None
        else None
    )
    cpu_before = _prometheus_value(metrics_before, "process_cpu_seconds_total")
    cpu_after = _prometheus_value(metrics_after, "process_cpu_seconds_total")
    cpu_percent = (
        max(0.0, (cpu_after - cpu_before) / max(elapsed, 0.001) * 100)
        if cpu_before is not None and cpu_after is not None
        else None
    )
    achieved = len(results) / dispatch_window * 60
    return {
        "alerts_per_minute": rate,
        "offered_alerts_per_minute": planned / duration_seconds * 60,
        "achieved_alerts_per_minute": round(achieved, 3),
        "arrival_rate_error_ratio": round(abs(achieved - rate) / rate, 6),
        "configured_duration_seconds": duration_seconds,
        "actual_duration_seconds": round(elapsed, 6),
        "actual_alerts_per_minute": round(achieved, 3),
        "simulated": False,
        "synthetic": synthetic,
        "measurement_source": "python-bounded-worker-fallback",
        "http_p95_ms": round(_percentile(latencies, 0.95), 3),
        "http_p99_ms": round(_percentile(latencies, 0.99), 3),
        "error_rate": round((failures + dropped) / planned, 6),
        "request_count": len(results),
        "dropped_iterations": dropped,
        "check_rate": round((planned - failures - dropped) / planned, 6),
        "status_code_distribution": dict(sorted(status_distribution.items())),
        "db_pool": None,
        "outbox_backlog": outbox_after,
        "outbox_backlog_before": outbox_before,
        "kafka_lag": kafka_after,
        "kafka_lag_before": kafka_before,
        "rca_queue_delay_ms": None,
        "completion_rate": completion,
        "cpu_percent": cpu_percent,
        "memory_bytes": _prometheus_value(
            metrics_after, "process_resident_memory_bytes"
        ),
        "known_limitations": [
            "k6 未安装时使用有界 Python Worker；生产验收优先使用 k6。",
            "DB pool 和 RCA queue delay 需要目标暴露对应 Prometheus 指标。",
        ],
    }


async def _scheduled_alert(
    client: httpx.AsyncClient,
    base_url: str,
    tenant_id: str,
    rate: int,
    index: int,
    *,
    scheduled_at: float,
    webhook_secret: str,
) -> _AlertResult:
    del scheduled_at
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
    timestamp = str(int(time()))
    signature = hmac.new(
        webhook_secret.encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-DevOps-Agent-Timestamp": timestamp,
        "X-DevOps-Agent-Signature": f"sha256={signature}",
    }
    started = perf_counter()
    try:
        response = await client.post(
            f"{base_url}/api/v1/alerts", content=body, headers=headers
        )
        status = response.status_code
        outcome = str(status)
    except httpx.TimeoutException:
        status = None
        outcome = "timeout"
    except httpx.HTTPError:
        status = None
        outcome = "transport_error"
    return _AlertResult(
        latency_ms=(perf_counter() - started) * 1000,
        status_code=status,
        outcome=outcome,
        started_at=started,
    )


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
    parser.add_argument("--duration-seconds", type=float, default=300)
    parser.add_argument("--tenant-id", default="reference-tenant")
    parser.add_argument("--max-workers", type=int, default=200)
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
            duration_seconds=args.duration_seconds,
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
                max_workers=args.max_workers,
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
