from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from devops_agent_platform.evaluation.schemas import ConclusionStatus, ToolStatus

PROJECT_ROOT = Path(__file__).resolve().parents[3]
E2E_ROOT = PROJECT_ROOT / "ops" / "minishop-e2e"


def _load_module():
    path = E2E_ROOT / "run_multi_incident.py"
    sys.path.insert(0, str(E2E_ROOT))
    spec = importlib.util.spec_from_file_location("run_multi_incident", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(E2E_ROOT))
    return module


def test_multi_incident_runner_detects_workflow_overlap() -> None:
    module = _load_module()

    assert module._workflows_overlap(
        {
            "started_at": "2026-08-21T10:00:00+00:00",
            "ended_at": "2026-08-21T10:01:00+00:00",
        },
        {
            "started_at": "2026-08-21T10:00:30+00:00",
            "ended_at": "2026-08-21T10:02:00+00:00",
        },
    )
    assert not module._workflows_overlap(
        {
            "started_at": "2026-08-21T10:00:00+00:00",
            "ended_at": "2026-08-21T10:01:00+00:00",
        },
        {
            "started_at": "2026-08-21T10:01:01+00:00",
            "ended_at": "2026-08-21T10:02:00+00:00",
        },
    )


def test_multi_incident_runner_uses_two_distinct_fault_scenarios() -> None:
    module = _load_module()

    assert module.SCENARIO_IDS == ("inventory-db-timeout", "payment-error")
    assert module.DIRECT_TRIGGERS["inventory-db-timeout"]["path"] == (
        "/inventory/reserve"
    )
    assert module.DIRECT_TRIGGERS["payment-error"]["path"] == "/payment/pay"


def test_multi_incident_runner_preserves_failed_workflow_as_prediction() -> None:
    module = _load_module()
    executor = module.MiniShopBlackBoxExecutor.__new__(
        module.MiniShopBlackBoxExecutor
    )
    scenario = module._load_scenarios(
        PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios" / "public"
    )[1]
    prediction = module._prediction_from_terminal_result(
        executor,
        scenario,
        {
            "workflow_run_id": "wfr-failed",
            "incident_id": "inc-failed",
            "status": "FAILED",
            "step_count": 1,
            "evidence": [],
            "invocations": [
                {
                    "tool_name": "traces.query",
                    "tool_version": "v1",
                    "status": "FAILED",
                    "step_id": "collect.traces",
                    "latency_ms": 25,
                }
            ],
        },
    )

    assert prediction.conclusion_status is ConclusionStatus.UNDETERMINED
    assert prediction.root_cause is None
    assert prediction.latency_ms == 25
    assert prediction.tool_calls[0].status is ToolStatus.FAILED
