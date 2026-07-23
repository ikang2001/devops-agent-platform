"""Run all MiniShop fault manifests through the real DevOps RCA workflow."""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from app.scenario_manifest import (
    ExpectedSignal,
    ScenarioManifest,
    load_scenario_catalog,
)

TERMINAL_WORKFLOW_STATES = {"SUCCEEDED", "FAILED", "CANCELED"}
REQUIRED_PLATFORM_EVIDENCE = {"METRIC", "LOG", "TRACE", "RUNBOOK"}
EXPECTED_SCENARIOS = {
    "checkout-latency",
    "inventory-db-timeout",
    "payment-error",
}


class E2EFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class E2EConfig:
    agent_url: str = os.getenv("E2E_AGENT_URL", "http://agent:8000")
    minishop_url: str = os.getenv("E2E_MINISHOP_URL", "http://minishop:8080")
    prometheus_url: str = os.getenv("E2E_PROMETHEUS_URL", "http://prometheus:9090")
    loki_url: str = os.getenv("E2E_LOKI_URL", "http://loki:3100")
    tempo_url: str = os.getenv("E2E_TEMPO_URL", "http://tempo:3200")
    alertmanager_url: str = os.getenv(
        "E2E_ALERTMANAGER_URL", "http://alertmanager:9093"
    )
    llm_url: str = os.getenv("E2E_LLM_URL", "http://llm-stub:8081")
    tenant_id: str = os.getenv("E2E_TENANT_ID", "demo")
    admin_id: str = os.getenv("E2E_ADMIN_ID", "demo-admin")
    admin_token: str = os.getenv(
        "E2E_ADMIN_TOKEN", "minishop-e2e-admin-token-change-me-123456"
    )
    timeout_seconds: float = float(os.getenv("E2E_TIMEOUT_SECONDS", "180"))
    artifacts_path: Path = Path(
        os.getenv("E2E_ARTIFACTS_PATH", "/artifacts/results.json")
    )


@dataclass(frozen=True)
class SignalResult:
    evidence_id: str
    source: str
    passed: bool
    assertion: str


class JsonHttpClient:
    def __init__(self) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return self._client.request(method, url, **kwargs)

    def require_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self.request(method, url, **kwargs)
        if not 200 <= response.status_code < 300:
            raise E2EFailure(f"{method} {url} returned HTTP {response.status_code}")
        document = response.json()
        if not isinstance(document, dict):
            raise E2EFailure(f"{method} {url} returned a non-object response")
        return document


class SignalEvaluator:
    def __init__(self, config: E2EConfig, http: JsonHttpClient) -> None:
        self._config = config
        self._http = http

    def evaluate(
        self,
        signal: ExpectedSignal,
        *,
        started_at: datetime,
        trigger_status: int,
        expected_trigger_status: int,
    ) -> SignalResult:
        evaluators: dict[str, Callable[[], bool]] = {
            "http": lambda: trigger_status == expected_trigger_status,
            "prometheus": lambda: self._prometheus(signal.locator),
            "loki": lambda: self._loki(signal.locator, started_at),
            "tempo": lambda: self._tempo(signal.locator, started_at),
        }
        passed = wait_for(
            f"signal {signal.evidence_id}",
            evaluators[signal.source],
            timeout_seconds=45,
            interval_seconds=2,
        )
        return SignalResult(
            evidence_id=signal.evidence_id,
            source=signal.source,
            passed=passed,
            assertion=signal.assertion,
        )

    def _prometheus(self, query: str) -> bool:
        document = self._http.require_json(
            "GET",
            f"{self._config.prometheus_url}/api/v1/query",
            params={"query": query},
        )
        results = document.get("data", {}).get("result", [])
        values = [item.get("value", [None, None])[1] for item in results]
        return any(_positive_number(value) for value in values)

    def _loki(self, query: str, started_at: datetime) -> bool:
        end = datetime.now(UTC)
        document = self._http.require_json(
            "GET",
            f"{self._config.loki_url}/loki/api/v1/query_range",
            params={
                "query": query,
                "start": str(
                    int((started_at - timedelta(seconds=10)).timestamp() * 1e9)
                ),
                "end": str(int(end.timestamp() * 1e9)),
                "limit": "100",
                "direction": "backward",
            },
        )
        streams = document.get("data", {}).get("result", [])
        return any(stream.get("values") for stream in streams)

    def _tempo(self, query: str, started_at: datetime) -> bool:
        document = self._http.require_json(
            "GET",
            f"{self._config.tempo_url}/api/search",
            params={
                "q": query,
                "start": str(int((started_at - timedelta(seconds=10)).timestamp())),
                "end": str(int(datetime.now(UTC).timestamp())),
                "limit": "20",
                "spss": "3",
            },
        )
        return bool(document.get("traces"))


class MiniShopE2ERunner:
    def __init__(self, config: E2EConfig) -> None:
        self.config = config
        self.http = JsonHttpClient()
        self.signal_evaluator = SignalEvaluator(config, self.http)
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

    def close(self) -> None:
        self.http.close()

    def run(self) -> dict[str, Any]:
        self._wait_for_stack()
        catalog = load_scenario_catalog(Path("/app/scenarios"))
        scenario_ids = {scenario.scenario_id for scenario in catalog.scenarios}
        if scenario_ids != EXPECTED_SCENARIOS:
            raise E2EFailure(f"unexpected scenario catalog: {sorted(scenario_ids)}")
        self._grant_tool_permissions()
        self._publish_runbooks(catalog.scenarios)
        results = [self._run_scenario(scenario) for scenario in catalog.scenarios]
        report = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "passed": all(result["passed"] for result in results),
            "scenarios": results,
        }
        self.config.artifacts_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.artifacts_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not report["passed"]:
            raise E2EFailure("one or more scenario evaluations failed")
        return report

    def _wait_for_stack(self) -> None:
        health_urls = {
            "agent": f"{self.config.agent_url}/readyz",
            "minishop": f"{self.config.minishop_url}/healthz",
            "prometheus": f"{self.config.prometheus_url}/-/ready",
            "loki": f"{self.config.loki_url}/ready",
            "tempo": f"{self.config.tempo_url}/ready",
            "alertmanager": f"{self.config.alertmanager_url}/-/ready",
            "llm-stub": f"{self.config.llm_url}/healthz",
        }
        for name, url in health_urls.items():
            if not wait_for(
                name,
                lambda target=url: self._is_healthy(target),
                timeout_seconds=self.config.timeout_seconds,
                interval_seconds=2,
            ):
                raise E2EFailure(f"{name} did not become ready")

    def _is_healthy(self, url: str) -> bool:
        try:
            response = self.http.request("GET", url)
        except httpx.RequestError:
            return False
        return 200 <= response.status_code < 300

    def _grant_tool_permissions(self) -> None:
        url = self._admin_url(f"operators/{self.config.admin_id}/tool-permissions")
        current = self.http.request("GET", url, headers=self._auth_headers())
        if current.status_code != 200:
            raise E2EFailure(
                f"tool permission read returned HTTP {current.status_code}"
            )
        etag = current.headers.get("ETag")
        if etag is None:
            raise E2EFailure("tool permission response did not include ETag")
        response = self.http.request(
            "PUT",
            url,
            headers=self._write_headers(f"permissions-{self.run_id}", etag),
            json={
                "permission_tags": [
                    "logs:read",
                    "metrics:read",
                    "runbooks:read",
                    "tenant:observe",
                    "traces:read",
                ]
            },
        )
        self._require_platform_success(response, "grant tool permissions")

    def _publish_runbooks(self, scenarios: list[ScenarioManifest]) -> None:
        for scenario in scenarios:
            runbook_key = f"minishop-{scenario.scenario_id}"
            base = self._admin_url(f"runbooks/{runbook_key}/versions/v1")
            draft = self.http.request(
                "PUT",
                f"{base}/draft",
                headers=self._write_headers(
                    f"runbook-draft-{scenario.scenario_id}-{self.run_id}", '"0"'
                ),
                json={
                    "service_name": scenario.service_name,
                    "title": scenario.title,
                    "summary": scenario.ground_truth.root_cause.summary,
                    "priority": 100,
                    "steps": scenario.ground_truth.root_cause.causal_chain,
                    "tags": ["minishop", scenario.fault_type, "e2e"],
                },
            )
            draft_data = self._require_platform_success(draft, "save runbook draft")
            publish = self.http.request(
                "POST",
                f"{base}/publish",
                headers=self._write_headers(
                    f"runbook-publish-{scenario.scenario_id}-{self.run_id}",
                    f'"{draft_data["revision"]}"',
                ),
            )
            self._require_platform_success(publish, "publish runbook")

    def _run_scenario(self, scenario: ScenarioManifest) -> dict[str, Any]:
        started_at = datetime.now(UTC)
        trigger_status = 0
        try:
            self._invoke_minishop(scenario.cleanup)
            self._invoke_minishop(scenario.injection)
            trigger_status = self._generate_fault_traffic(scenario)
            incident = self._wait_for_incident(scenario, started_at)
            workflow_run_id = self._start_rca(incident["incident_id"], scenario)
            result = self._wait_for_workflow(workflow_run_id)
            return self._evaluate_scenario(
                scenario,
                result,
                started_at=started_at,
                trigger_status=trigger_status,
            )
        finally:
            self._invoke_minishop(scenario.cleanup)

    def _generate_fault_traffic(self, scenario: ScenarioManifest) -> int:
        attempts = 3 if scenario.scenario_id != "payment-error" else 6
        status = 0
        for index in range(attempts):
            body = dict(scenario.trigger.json_body)
            if isinstance(body.get("idempotency_key"), str):
                body["idempotency_key"] = (
                    f"{body['idempotency_key']}-{self.run_id}-{index}"
                )
            trace_id = f"e2e-{scenario.scenario_id}-{index}-{uuid.uuid4().hex[:8]}"
            response = self.http.request(
                scenario.trigger.method,
                f"{self.config.minishop_url}{scenario.trigger.path}",
                json=body,
                headers={"X-Trace-Id": trace_id},
            )
            status = response.status_code
            if status != scenario.trigger.expected_status:
                raise E2EFailure(
                    f"{scenario.scenario_id} trigger expected "
                    f"{scenario.trigger.expected_status}, got {status}"
                )
        return status

    def _wait_for_incident(
        self, scenario: ScenarioManifest, started_at: datetime
    ) -> dict[str, Any]:
        def find_incident() -> dict[str, Any] | None:
            response = self.http.request(
                "GET",
                self._admin_url("incidents"),
                headers=self._auth_headers(),
                params={"limit": "100"},
            )
            data = self._require_platform_success(response, "list incidents")
            for incident in data["items"]:
                created_at = datetime.fromisoformat(incident["created_at"])
                if incident[
                    "service_name"
                ] == scenario.service_name and created_at >= started_at - timedelta(
                    seconds=10
                ):
                    return incident
            return None

        incident = wait_for_value(
            f"incident for {scenario.scenario_id}",
            find_incident,
            timeout_seconds=self.config.timeout_seconds,
            interval_seconds=2,
        )
        if incident is None:
            raise E2EFailure(f"incident was not created for {scenario.scenario_id}")
        return incident

    def _start_rca(self, incident_id: str, scenario: ScenarioManifest) -> str:
        response = self.http.request(
            "POST",
            self._admin_url(f"incidents/{incident_id}/rca"),
            headers={
                **self._auth_headers(),
                "Idempotency-Key": f"rca-{scenario.scenario_id}-{self.run_id}",
            },
            json={},
        )
        data = self._require_platform_success(response, "start RCA")
        return data["workflow_run_id"]

    def _wait_for_workflow(self, workflow_run_id: str) -> dict[str, Any]:
        def terminal_result() -> dict[str, Any] | None:
            response = self.http.request(
                "GET",
                self._admin_url(f"workflow-runs/{workflow_run_id}/result"),
                headers=self._auth_headers(),
            )
            data = self._require_platform_success(response, "read RCA result")
            return data if data["status"] in TERMINAL_WORKFLOW_STATES else None

        result = wait_for_value(
            f"workflow {workflow_run_id}",
            terminal_result,
            timeout_seconds=self.config.timeout_seconds,
            interval_seconds=2,
        )
        if result is None:
            raise E2EFailure(f"workflow {workflow_run_id} did not finish")
        if result["status"] != "SUCCEEDED":
            raise E2EFailure(f"workflow {workflow_run_id} ended as {result['status']}")
        return result

    def _evaluate_scenario(
        self,
        scenario: ScenarioManifest,
        result: dict[str, Any],
        *,
        started_at: datetime,
        trigger_status: int,
    ) -> dict[str, Any]:
        signal_results = [
            self.signal_evaluator.evaluate(
                signal,
                started_at=started_at,
                trigger_status=trigger_status,
                expected_trigger_status=scenario.trigger.expected_status,
            )
            for signal in scenario.expected_signals
        ]
        signal_by_id = {signal.evidence_id: signal for signal in signal_results}
        required_passed = all(
            signal_by_id[evidence_id].passed
            for evidence_id in scenario.ground_truth.required_evidence
        )
        evidence_types = {item["evidence_type"] for item in result["evidence"]}
        invocations_passed = all(
            item["status"] == "SUCCEEDED" for item in result["invocations"]
        )
        report = result.get("report") or {}
        report_text = f"{report.get('title', '')} {report.get('summary', '')}".lower()
        root_cause_passed = (
            report.get("conclusion_status") == "CANDIDATE"
            and scenario.service_name.lower() in report_text
            and scenario.fault_type.lower() in report_text
        )
        forbidden_claims = [
            claim
            for claim in scenario.ground_truth.forbidden_claims
            if claim.lower() in report_text
        ]
        platform_evidence_passed = REQUIRED_PLATFORM_EVIDENCE.issubset(evidence_types)
        passed = all(
            (
                required_passed,
                platform_evidence_passed,
                invocations_passed,
                root_cause_passed,
                not forbidden_claims,
            )
        )
        return {
            "scenario_id": scenario.scenario_id,
            "passed": passed,
            "incident_id": result["incident_id"],
            "workflow_run_id": result["workflow_run_id"],
            "workflow_status": result["status"],
            "platform_evidence_types": sorted(evidence_types),
            "platform_evidence_passed": platform_evidence_passed,
            "invocations_passed": invocations_passed,
            "root_cause_passed": root_cause_passed,
            "forbidden_claims_found": forbidden_claims,
            "required_evidence_passed": required_passed,
            "signals": [asdict(signal) for signal in signal_results],
            "report": {
                "conclusion_status": report.get("conclusion_status"),
                "title": report.get("title"),
                "summary": report.get("summary"),
                "confidence": report.get("confidence"),
            },
        }

    def _invoke_minishop(self, action: Any) -> dict[str, Any]:
        response = self.http.request(
            action.method,
            f"{self.config.minishop_url}{action.path}",
            json=action.json_body,
        )
        if response.status_code != action.expected_status:
            raise E2EFailure(
                f"{action.method} {action.path} expected {action.expected_status}, "
                f"got {response.status_code}"
            )
        document = response.json()
        return document if isinstance(document, dict) else {}

    def _admin_url(self, suffix: str) -> str:
        return (
            f"{self.config.agent_url}/api/v1/admin/tenants/"
            f"{self.config.tenant_id}/{suffix}"
        )

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.admin_token}"}

    def _write_headers(self, idempotency_key: str, etag: str) -> dict[str, str]:
        return {
            **self._auth_headers(),
            "Idempotency-Key": idempotency_key,
            "If-Match": etag,
        }

    @staticmethod
    def _require_platform_success(
        response: httpx.Response, operation: str
    ) -> dict[str, Any]:
        if not 200 <= response.status_code < 300:
            raise E2EFailure(f"{operation} returned HTTP {response.status_code}")
        document = response.json()
        if not isinstance(document, dict) or document.get("success") is not True:
            raise E2EFailure(f"{operation} returned an invalid platform envelope")
        data = document.get("data")
        if not isinstance(data, dict):
            raise E2EFailure(f"{operation} returned invalid platform data")
        return data


def wait_for(
    label: str,
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float,
    interval_seconds: float,
) -> bool:
    del label
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except (E2EFailure, httpx.RequestError, ValueError, KeyError, TypeError):
            pass
        time.sleep(interval_seconds)
    return False


def wait_for_value(
    label: str,
    supplier: Callable[[], dict[str, Any] | None],
    *,
    timeout_seconds: float,
    interval_seconds: float,
) -> dict[str, Any] | None:
    del label
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            value = supplier()
            if value is not None:
                return value
        except (E2EFailure, httpx.RequestError, ValueError, KeyError, TypeError):
            pass
        time.sleep(interval_seconds)
    return None


def _positive_number(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def main() -> int:
    runner = MiniShopE2ERunner(E2EConfig())
    try:
        report = runner.run()
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        print("MINISHOP_E2E_PASSED=1", flush=True)
        return 0
    except E2EFailure as exc:
        print(f"MINISHOP_E2E_FAILED={exc}", flush=True)
        return 1
    finally:
        runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
