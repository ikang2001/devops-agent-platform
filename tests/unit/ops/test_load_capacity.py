from __future__ import annotations

import json

import httpx
import pytest

from ops.load.run_load import (
    _scheduled_alert,
    evaluate_end_to_end_gate,
    evaluate_ingestion_gate,
    run_http_load_matrix,
)
from ops.simulation.run_simulation import _attach_completion_probe


def _passing_profile() -> dict[str, object]:
    return {
        "alerts_per_minute": 100,
        "achieved_alerts_per_minute": 101,
        "configured_duration_seconds": 300,
        "error_rate": 0.001,
        "dropped_iterations": 0,
        "http_p95_ms": 120,
        "http_p99_ms": 240,
        "completion_rate": 1.0,
        "outbox_backlog": 0,
        "kafka_lag": 0,
    }


def test_capacity_gates_distinguish_ingestion_from_end_to_end() -> None:
    profile = _passing_profile()

    assert evaluate_ingestion_gate(profile)["passed"] is True
    assert evaluate_end_to_end_gate(profile)["passed"] is True

    profile["completion_rate"] = None
    assert evaluate_ingestion_gate(profile)["passed"] is True
    assert evaluate_end_to_end_gate(profile)["passed"] is False


def test_capacity_gate_rejects_short_or_slow_samples() -> None:
    profile = _passing_profile()
    profile.update(
        {
            "configured_duration_seconds": 6,
            "achieved_alerts_per_minute": 68,
            "error_rate": 1.0,
            "dropped_iterations": 2,
            "http_p95_ms": 900,
            "http_p99_ms": 1200,
        }
    )

    result = evaluate_ingestion_gate(profile)

    assert result["passed"] is False
    assert result["checks"] == {
        "duration_seconds_at_least_60": False,
        "arrival_rate_within_5_percent": False,
        "error_rate_below_1_percent": False,
        "dropped_iterations_zero": False,
        "p95_below_500_ms_and_p99_below_1000_ms": False,
    }


def test_simulation_completion_probe_closes_load_gate(tmp_path) -> None:
    load_path = tmp_path / "load-report.json"
    probe_path = tmp_path / "rca-completion-probe.json"
    load_path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        **_passing_profile(),
                        "configured_duration_seconds": 60,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    probe_path.write_text(
        json.dumps(
            {
                "completion_rate": 1.0,
                "requested_workflows": 3,
                "completed_workflows": 3,
            }
        ),
        encoding="utf-8",
    )

    _attach_completion_probe(load_path, probe_path)

    document = json.loads(load_path.read_text(encoding="utf-8"))
    profile = document["profiles"][0]
    assert profile["completion_rate"] == 1.0
    assert profile["completion_measurement_source"] == "rca-completion-probe"
    assert profile["end_to_end_gate"]["passed"] is True
    assert document["completion_probe"]["completed_workflows"] == 3


@pytest.mark.asyncio
async def test_http_load_requires_hmac_secret_before_sending() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        await run_http_load_matrix(
            target_url="http://agent.test",
            duration_seconds=1,
            rates=(60,),
            webhook_secret="too-short",
        )


@pytest.mark.asyncio
async def test_scheduled_alert_always_signs_the_exact_request_body() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await _scheduled_alert(
            client,
            "http://agent.test",
            "tenant-1",
            60,
            1,
            scheduled_at=0,
            webhook_secret="x" * 32,
        )
    finally:
        await client.aclose()

    assert result.status_code == 200
    assert requests[0].headers["X-DevOps-Agent-Signature"].startswith("sha256=")
    assert requests[0].headers["X-DevOps-Agent-Timestamp"].isdigit()
