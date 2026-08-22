"""Run the v0.7 same-tenant multi-incident isolation experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blackbox_executor import MiniShopBlackBoxExecutor
from run_e2e import E2EConfig, E2EFailure, JsonHttpClient, wait_for_value

from devops_agent_platform.evaluation.integrity import (
    BenchmarkLeakageGuard,
    BenchmarkProvenance,
    cross_incident_evidence_leak_rate,
    hash_directory,
)
from devops_agent_platform.evaluation.runner import run_benchmark_input
from devops_agent_platform.evaluation.scenario_catalog import (
    PublicScenario,
    load_public_scenarios,
)
from devops_agent_platform.evaluation.schemas import (
    BenchmarkInput,
    BenchmarkMetadata,
    ConclusionStatus,
    RCAPrediction,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_IDS = ("inventory-db-timeout", "payment-error")
DIRECT_TRIGGERS = {
    "inventory-db-timeout": {
        "method": "POST",
        "path": "/inventory/reserve",
        "expected_status": 503,
        "json_body": {"sku": "multi-inventory-001", "quantity": 1},
    },
    "payment-error": {
        "method": "POST",
        "path": "/payment/pay",
        "expected_status": 500,
        "json_body": {
            "order_id": "multi-payment-order",
            "amount": 99.0,
            "currency": "CNY",
        },
    },
}


class MultiIncidentExperiment:
    """Keep two faults active while two incidents and RCA workflows coexist."""

    def __init__(
        self,
        executor: MiniShopBlackBoxExecutor,
        scenarios: tuple[PublicScenario, PublicScenario],
    ) -> None:
        self.executor = executor
        self.scenarios = scenarios

    def run_pair(
        self,
        pair_index: int,
    ) -> tuple[tuple[RCAPrediction, RCAPrediction], dict[str, object]]:
        self.executor._prepare_once()
        started_at = datetime.now(UTC)
        incidents: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        try:
            self.executor._invoke_action(self.scenarios[0].cleanup)
            for scenario in self.scenarios:
                self.executor._invoke_action(scenario.injection)
            self._trigger_both(pair_index)
            incidents = [
                self.executor._wait_for_incident(scenario, started_at)
                for scenario in self.scenarios
            ]
            incident_ids = tuple(str(item["incident_id"]) for item in incidents)
            if len(set(incident_ids)) != 2:
                raise E2EFailure(
                    "multi-incident experiment did not create two distinct incidents"
                )
            workflow_ids = tuple(
                self.executor._start_rca(incident_id, scenario)
                for incident_id, scenario in zip(
                    incident_ids, self.scenarios, strict=True
                )
            )
            if len(set(workflow_ids)) != 2:
                raise E2EFailure(
                    "multi-incident experiment did not create two distinct workflows"
                )
            # Both workflows have been accepted before either result is awaited.
            # The platform may schedule them on one or more consumers; the raw
            # timestamps below preserve whether their execution windows overlapped.
            results = [
                self._wait_for_terminal_result(workflow_id)
                for workflow_id in workflow_ids
            ]
            predictions = tuple(
                _prediction_from_terminal_result(self.executor, scenario, result)
                for scenario, result in zip(self.scenarios, results, strict=True)
            )
            integrity = _integrity_record(
                pair_index=pair_index,
                tenant_id=self.executor.config.tenant_id,
                incidents=incidents,
                results=results,
                predictions=predictions,
            )
            return predictions, integrity
        finally:
            self.executor._invoke_action(self.scenarios[0].cleanup)
            for scenario in self.scenarios:
                self.executor._wait_for_alert_clear(scenario)
            for incident in incidents:
                self.executor._settle_incident(str(incident["incident_id"]))

    def _wait_for_terminal_result(self, workflow_run_id: str) -> dict[str, Any]:
        """Preserve FAILED/CANCELED workflows as scored experiment samples."""

        def terminal() -> dict[str, Any] | None:
            response = self.executor.http.request(
                "GET",
                self.executor._admin_url(
                    f"workflow-runs/{workflow_run_id}/result"
                ),
                headers=self.executor._auth_headers(),
            )
            data = self.executor._require_platform_success(
                response, "read multi-incident RCA"
            )
            status = data.get("status")
            if status in {"FAILED", "CANCELED"}:
                return data
            if status != "SUCCEEDED":
                return None
            if not data.get("evidence") or not isinstance(data.get("report"), dict):
                return None
            return data

        result = wait_for_value(
            f"multi-incident workflow {workflow_run_id}",
            terminal,
            timeout_seconds=self.executor.config.timeout_seconds,
            interval_seconds=2,
        )
        if result is None:
            raise E2EFailure(f"workflow {workflow_run_id} did not reach terminal state")
        return result

    def _trigger_both(self, pair_index: int) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    _invoke_direct_trigger,
                    self.executor.config,
                    DIRECT_TRIGGERS[scenario.scenario_id],
                    pair_index,
                    scenario.scenario_id,
                )
                for scenario in self.scenarios
            ]
            for future in futures:
                future.result()


def _invoke_direct_trigger(
    config: E2EConfig,
    action: dict[str, object],
    pair_index: int,
    scenario_id: str,
) -> None:
    client = JsonHttpClient()
    try:
        response = client.request(
            str(action["method"]),
            f"{config.minishop_url}{action['path']}",
            json=action["json_body"],
            headers={
                "X-Trace-Id": (
                    f"multi-{pair_index}-{scenario_id}-{uuid.uuid4().hex}"
                )
            },
        )
    finally:
        client.close()
    if response.status_code != action["expected_status"]:
        raise E2EFailure(
            f"multi-incident trigger {scenario_id} expected "
            f"{action['expected_status']}, got {response.status_code}"
        )


def _integrity_record(
    *,
    pair_index: int,
    tenant_id: str,
    incidents: list[dict[str, Any]],
    results: list[dict[str, Any]],
    predictions: tuple[RCAPrediction, RCAPrediction],
) -> dict[str, object]:
    evidence_sets = [set(item.evidence_ids) for item in predictions]
    overlap = sorted(evidence_sets[0] & evidence_sets[1])
    traces = tuple(str(item.get("trace_id", "")) for item in results)
    return {
        "pair_index": pair_index,
        "tenant_id": tenant_id,
        "incident_ids": [str(item["incident_id"]) for item in incidents],
        "workflow_run_ids": [str(item["workflow_run_id"]) for item in results],
        "workflow_trace_ids": list(traces),
        "workflow_statuses": [str(item.get("status", "")) for item in results],
        "pair_succeeded": all(item.get("status") == "SUCCEEDED" for item in results),
        "incident_ids_distinct": len({item["incident_id"] for item in incidents}) == 2,
        "workflow_run_ids_distinct": (
            len({item["workflow_run_id"] for item in results}) == 2
        ),
        "workflow_trace_ids_distinct": len(set(traces)) == 2 and all(traces),
        "workflows_overlapped": _workflows_overlap(results[0], results[1]),
        "evidence_ids_by_incident": {
            str(prediction.incident_id): sorted(prediction.evidence_ids)
            for prediction in predictions
        },
        "cross_incident_evidence_ids": overlap,
        "cross_incident_evidence_leak": bool(overlap),
    }


def _prediction_from_terminal_result(
    executor: MiniShopBlackBoxExecutor,
    scenario: PublicScenario,
    result: dict[str, Any],
) -> RCAPrediction:
    if result.get("status") == "SUCCEEDED":
        return executor._prediction(scenario, result)

    evidence = tuple(
        item for item in result.get("evidence", []) if isinstance(item, dict)
    )
    invocations = tuple(
        item for item in result.get("invocations", []) if isinstance(item, dict)
    )
    tool_calls = tuple(
        executor._terminal_tool_call(item, evidence) for item in invocations
    )
    return RCAPrediction(
        scenario_id=scenario.scenario_id,
        incident_id=str(result["incident_id"]),
        run_id=str(result["workflow_run_id"]),
        root_cause=None,
        conclusion_status=ConclusionStatus.UNDETERMINED,
        confidence=0.0,
        evidence_ids=(),
        evidence_types=(),
        claims=(),
        causal_chain=(),
        affected_services=(),
        root_cause_candidates=(),
        tool_calls=tool_calls,
        investigation_steps=max(int(result.get("step_count", 0)), len(tool_calls)),
        llm_calls=0,
        latency_ms=sum(int(item.get("latency_ms", 0)) for item in invocations),
        total_tokens=0,
        estimated_cost=0.0,
    )


def _workflows_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_start = datetime.fromisoformat(str(left["started_at"]))
    left_end = datetime.fromisoformat(str(left["ended_at"]))
    right_start = datetime.fromisoformat(str(right["started_at"]))
    right_end = datetime.fromisoformat(str(right["ended_at"]))
    return max(left_start, right_start) <= min(left_end, right_end)


def _load_scenarios(public_directory: Path) -> tuple[PublicScenario, PublicScenario]:
    by_id = {
        scenario.scenario_id: scenario
        for scenario in load_public_scenarios(public_directory)
    }
    try:
        return by_id[SCENARIO_IDS[0]], by_id[SCENARIO_IDS[1]]
    except KeyError as exc:
        raise ValueError(f"multi-incident scenario is missing: {exc.args[0]}") from exc


def _score(
    *,
    predictions: tuple[RCAPrediction, ...],
    private_directory: Path,
    output_directory: Path,
    git_commit: str,
    provider: str,
    model: str,
    runs_per_scenario: int,
    integrity: list[dict[str, object]],
    temporary_root: Path,
) -> dict[str, Any]:
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="multi-incident-private-",
        dir=temporary_root,
    ) as directory:
        subset = Path(directory)
        for scenario_id in SCENARIO_IDS:
            shutil.copy2(
                private_directory / f"{scenario_id}.json",
                subset / f"{scenario_id}.json",
            )
        metadata = BenchmarkMetadata(
            benchmark_version="0.7.0-multi-incident",
            git_commit=git_commit,
            model_provider=provider,
            model_name=model,
            temperature=0,
            top_p=1,
            max_tokens=0,
            prompt_version="platform-workflow-terminal-report",
            investigation_policy="multi-incident-blackbox-v1",
            scenario_version="1.0",
            timestamp=datetime.now(UTC),
            prompt_hash=hashlib.sha256(
                b"platform-workflow-terminal-report"
            ).hexdigest(),
        )
        return run_benchmark_input(
            subset,
            BenchmarkInput(benchmark=metadata, runs=predictions),
            output_directory,
            include_extended=True,
            execution_metadata={
                "mode": "multi_incident_blackbox",
                "runs_per_scenario": runs_per_scenario,
                "incident_pair_count": len(integrity),
                "workflows_overlapped_count": sum(
                    bool(item["workflows_overlapped"]) for item in integrity
                ),
                "cross_incident_evidence_leak_rate": (
                    cross_incident_evidence_leak_rate(predictions)
                ),
                "benchmark_leakage_violation_count": 0,
            },
        )


def _write_provenance(
    *,
    output_directory: Path,
    public_directory: Path,
    private_directory: Path,
    git_commit: str,
    provider: str,
    model: str,
    runs_per_scenario: int,
) -> None:
    provenance = BenchmarkProvenance.create(
        run_id=output_directory.name,
        benchmark_version="0.7.0-multi-incident",
        git_commit=git_commit,
        public_scenario_hash=hash_directory(public_directory),
        private_ground_truth_hash=hash_directory(private_directory),
        prompt_hash=hashlib.sha256(
            b"platform-workflow-terminal-report"
        ).hexdigest(),
        taxonomy_hash=hashlib.sha256(
            (
                PROJECT_ROOT
                / "src/devops_agent_platform/rca_reasoning/taxonomy.py"
            ).read_bytes()
        ).hexdigest(),
        reasoning_pipeline_hash=hashlib.sha256(
            (
                PROJECT_ROOT
                / "src/devops_agent_platform/rca_reasoning/pipeline.py"
            ).read_bytes()
        ).hexdigest(),
        model_provider=provider,
        model=model,
        temperature=0,
        runs_per_scenario=runs_per_scenario,
    )
    provenance.write(output_directory / "benchmark-provenance.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", required=True, type=Path)
    parser.add_argument("--private", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--provider", default="dashscope")
    parser.add_argument("--model", default="qwen3.7-plus")
    parser.add_argument("--runs-per-scenario", type=int, default=5)
    parser.add_argument(
        "--temporary-root",
        type=Path,
        default=Path("D:/DevOpsAgentTemp/v07"),
    )
    args = parser.parse_args(argv)
    if args.runs_per_scenario != 5:
        raise ValueError("formal multi-incident mode requires five pairs")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("multi-incident output directory must be empty")

    executor = MiniShopBlackBoxExecutor(E2EConfig())
    predictions: list[RCAPrediction] = []
    integrity: list[dict[str, object]] = []
    try:
        experiment = MultiIncidentExperiment(
            executor,
            _load_scenarios(args.public),
        )
        for pair_index in range(1, args.runs_per_scenario + 1):
            pair_predictions, pair_integrity = experiment.run_pair(pair_index)
            predictions.extend(pair_predictions)
            integrity.append(pair_integrity)
    finally:
        executor.close()

    BenchmarkLeakageGuard().assert_clean(integrity, source="multi-incident-runtime")
    result = _score(
        predictions=tuple(predictions),
        private_directory=args.private,
        output_directory=args.output,
        git_commit=args.git_commit,
        provider=args.provider,
        model=args.model,
        runs_per_scenario=args.runs_per_scenario,
        integrity=integrity,
        temporary_root=args.temporary_root,
    )
    integrity_document = {
        "schema_version": "1.0",
        "scenario_ids": list(SCENARIO_IDS),
        "pairs": integrity,
        "summary": {
            "pair_count": len(integrity),
            "distinct_incident_pair_count": sum(
                bool(item["incident_ids_distinct"]) for item in integrity
            ),
            "distinct_workflow_pair_count": sum(
                bool(item["workflow_run_ids_distinct"]) for item in integrity
            ),
            "distinct_trace_pair_count": sum(
                bool(item["workflow_trace_ids_distinct"]) for item in integrity
            ),
            "overlapped_workflow_pair_count": sum(
                bool(item["workflows_overlapped"]) for item in integrity
            ),
            "successful_pair_count": sum(
                bool(item["pair_succeeded"]) for item in integrity
            ),
            "failed_pair_count": sum(
                not bool(item["pair_succeeded"]) for item in integrity
            ),
            "cross_incident_evidence_leak_rate": (
                cross_incident_evidence_leak_rate(predictions)
            ),
        },
    }
    (args.output / "multi-incident-integrity.json").write_text(
        json.dumps(integrity_document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    _write_provenance(
        output_directory=args.output,
        public_directory=args.public,
        private_directory=args.private,
        git_commit=args.git_commit,
        provider=args.provider,
        model=args.model,
        runs_per_scenario=args.runs_per_scenario,
    )
    print(result["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
