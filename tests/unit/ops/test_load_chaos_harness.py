from __future__ import annotations

import json

import pytest

from ops.chaos.harness import load_live_results, run_all, run_case, write_report
from ops.load.run_load import (
    _percentile,
    _prometheus_sum,
    _prometheus_value,
    build_k6_load_report,
    build_local_load_report,
)


def test_load_plan_keeps_real_environment_metrics_empty() -> None:
    report = build_local_load_report()

    assert [item["alerts_per_minute"] for item in report["profiles"]] == [
        100,
        500,
        1000,
    ]
    for profile in report["profiles"]:
        assert profile["simulated"] is True
        assert profile["http_p95_ms"] is None
        assert profile["completion_rate"] is None


def test_load_plan_rejects_non_positive_rates() -> None:
    with pytest.raises(ValueError, match="positive"):
        build_local_load_report((100, 0))


def test_live_load_report_aggregates_k6_without_inventing_infra(
    tmp_path,
) -> None:
    metrics = {
        "metrics": {
            "http_req_duration": {"values": {"p(95)": 123.4}},
            "http_req_failed": {"values": {"rate": 0.01}},
            "http_reqs": {"values": {"count": 10}},
            "checks": {"values": {"rate": 0.99}},
        }
    }
    for rate in (100, 500, 1000):
        (tmp_path / f"load-{rate}.json").write_text(
            json.dumps(metrics), encoding="utf-8"
        )
    (tmp_path / "infra-100.json").write_text(
        json.dumps({"outbox_backlog": 2, "kafka_lag": 1}), encoding="utf-8"
    )

    report = build_k6_load_report(tmp_path, target_label="test-target")

    assert report["mode"] == "live"
    assert report["simulated"] is False
    assert report["profiles"][0]["http_p95_ms"] == 123.4
    assert report["profiles"][0]["outbox_backlog"] == 2
    assert report["profiles"][1]["outbox_backlog"] is None


def test_reference_fallback_metric_parsing_is_deterministic() -> None:
    metrics = "\n".join(
        (
            'devops_agent_outbox_backlog_events{status="PENDING"} 2',
            'devops_agent_outbox_backlog_events{status="FAILED"} 1',
            'devops_agent_rca_consumer_lag_records{scope="total"} 4',
            "process_resident_memory_bytes 1024",
        )
    )

    assert _percentile([1, 2, 3, 4], 0.95) == 4
    assert _prometheus_sum(metrics, "devops_agent_outbox_backlog_events") == 3
    assert (
        _prometheus_value(
            metrics,
            'devops_agent_rca_consumer_lag_records{scope="total"}',
        )
        == 4
    )


def test_chaos_harness_covers_all_declared_cases() -> None:
    results = run_all()

    assert {item.case for item in results} == {
        "worker-crash",
        "kafka-unavailable",
        "postgres-unavailable",
        "observability-timeout",
    }
    assert all(item.simulated for item in results)
    assert run_case("worker-crash").fence_rejection_count is None
    assert run_case("worker-crash").recovery_seconds is None


def test_chaos_harness_rejects_unknown_case_and_writes_json(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported chaos case"):
        run_case("unknown")

    output = tmp_path / "chaos.json"
    report = write_report(output)
    assert report["simulated"] is True
    assert report["passed"] is None
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_live_chaos_observations_are_validated_and_scored(tmp_path) -> None:
    source = tmp_path / "live.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "simulated": False,
                "results": [
                    {
                        "case": "worker-crash",
                        "recovery_seconds": 12.5,
                        "lost_workflow_count": 0,
                        "duplicate_completion_count": 0,
                        "fence_rejection_count": 1,
                    },
                    {
                        "case": "kafka-unavailable",
                        "outbox_recovery_seconds": 8,
                        "lost_event_count": 0,
                        "duplicate_workflow_count": 0,
                    },
                    {
                        "case": "postgres-unavailable",
                        "recovery_seconds": 4,
                        "state_corruption_count": 0,
                        "erroneous_success_count": 0,
                        "resumed": True,
                    },
                    {
                        "case": "observability-timeout",
                        "partial_report": True,
                        "confidence_capped": True,
                        "confirmed_count": 0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    results = load_live_results(source)

    assert all(item.passed for item in results)
    assert all(not item.simulated for item in results)

    report = write_report(tmp_path / "live-report.json", mode="live", live_input=source)
    assert report["synthetic"] is True
    assert report["production_acceptance"] is False
    assert report["target_label"] == "unspecified-live-target"
