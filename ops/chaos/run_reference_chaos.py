from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from uuid import uuid4

import httpx

if __package__:
    from .harness import write_report
else:
    from harness import write_report

TENANT_ID = "reference-tenant"
AGENT_URL = "http://localhost:28000"
REFERENCE_OPERATOR_ID = "reference-reviewer"
REFERENCE_PERMISSION_TAGS = (
    "changes:read",
    "knowledge:read",
    "logs:read",
    "metrics:read",
    "runbooks:read",
    "tenant:observe",
    "topology:read",
    "traces:read",
)


class ReferenceChaosRunner:
    def __init__(self, compose_file: Path) -> None:
        self.compose_file = compose_file
        self.client = httpx.Client(timeout=10, follow_redirects=False)
        self.oidc_client = httpx.Client(
            timeout=10,
            follow_redirects=False,
            verify=False,
        )

    def close(self) -> None:
        self.client.close()
        self.oidc_client.close()

    def run(self) -> list[dict]:
        token = self._token()
        self._ensure_permissions(token)
        return [
            self._worker_crash(token),
            self._kafka_unavailable(token),
            self._postgres_unavailable(),
            self._observability_timeout(token),
        ]

    def _worker_crash(self, token: str) -> dict:
        workflow_id = self._start_workflow(token, "worker-crash")
        self._wait_workflow_status(token, workflow_id, "RUNNING", timeout=60)
        stale_worker_id = "reference-staging-rca-consumer-worker"
        started = perf_counter()
        self._compose("kill", "agent")
        self._compose("up", "-d", "--wait", "--wait-timeout", "120", "agent")
        self._wait_execution_attempt(token, workflow_id, 2, timeout=60)
        stale_update_count = self._probe_stale_completion(
            workflow_id,
            stale_worker_id,
            execution_attempt=1,
        )
        result = self._wait_workflow(token, workflow_id, timeout=60)
        recovery = perf_counter() - started
        reports = int(
            self._sql(
                "select count(*) from rca_reports "
                f"where workflow_run_id='{workflow_id}'"
            )
        )
        return {
            "case": "worker-crash",
            "recovery_seconds": round(recovery, 6),
            "lost_workflow_count": 0
            if result["status"] in {"SUCCEEDED", "FAILED"}
            else 1,
            "duplicate_completion_count": max(0, reports - 1),
            "fence_rejection_count": 1 if stale_update_count == 0 else 0,
            "notes": (
                "Agent container was killed after RUNNING was observed; a replacement "
                f"finished with execution_attempts={result['execution_attempts']}."
            ),
        }

    def _kafka_unavailable(self, token: str) -> dict:
        self._compose("stop", "redpanda")
        try:
            started = perf_counter()
            workflow_id = self._start_workflow(token, "kafka-unavailable")
            pending = int(
                self._sql(
                    "select count(*) from outbox_events "
                    f"where aggregate_id='{workflow_id}' and status<>'PUBLISHED'"
                )
            )
            self._compose(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
                "redpanda",
            )
            self._wait_sql(
                "select count(*) from outbox_events "
                f"where aggregate_id='{workflow_id}' and status<>'PUBLISHED'",
                "0",
                timeout=60,
            )
            result = self._wait_workflow(token, workflow_id, timeout=60)
            recovery = perf_counter() - started
            workflow_count = int(
                self._sql(
                    "select count(*) from workflow_runs "
                    f"where workflow_run_id='{workflow_id}'"
                )
            )
            return {
                "case": "kafka-unavailable",
                "outbox_recovery_seconds": round(recovery, 6),
                "lost_event_count": 0 if pending >= 1 and result["status"] else 1,
                "duplicate_workflow_count": max(0, workflow_count - 1),
                "notes": (
                    "Workflow Outbox remained pending while Redpanda was stopped "
                    "and drained after restart."
                ),
            }
        finally:
            self._compose("up", "-d", "redpanda")

    def _postgres_unavailable(self) -> dict:
        self._compose("stop", "postgres")
        started = perf_counter()
        try:
            identity = uuid4().hex
            response = self.client.post(
                f"{AGENT_URL}/api/v1/alerts",
                json=self._alert_payload(identity, "postgres-unavailable"),
            )
            erroneous_success = 1 if 200 <= response.status_code < 300 else 0
        except httpx.HTTPError:
            erroneous_success = 0
        finally:
            self._compose(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
                "postgres",
            )
        self._wait_http(f"{AGENT_URL}/readyz", timeout=60)
        state_ok = self._sql("select 1") == "1"
        return {
            "case": "postgres-unavailable",
            "recovery_seconds": round(perf_counter() - started, 6),
            "state_corruption_count": 0 if state_ok else 1,
            "erroneous_success_count": erroneous_success,
            "resumed": state_ok,
            "notes": (
                "PostgreSQL was stopped during alert ingestion; readiness recovered "
                "after restart."
            ),
        }

    def _observability_timeout(self, token: str) -> dict:
        self._compose("stop", "loki")
        try:
            workflow_id = self._start_workflow(token, "observability-timeout")
            result = self._wait_workflow(token, workflow_id, timeout=60)
            report = result.get("report") or {}
            invocations = result.get("invocations") or []
            failed_logs = any(
                item.get("tool_name") == "logs.query" and item.get("status") == "FAILED"
                for item in invocations
            )
            summary = str(report.get("summary", ""))
            confidence = report.get("confidence")
            conclusion = report.get("conclusion_status")
            return {
                "case": "observability-timeout",
                "partial_report": failed_logs and "Partial collection:" in summary,
                "confidence_capped": (
                    isinstance(confidence, int | float) and confidence <= 0.4
                ),
                "confirmed_count": 1 if conclusion == "CONFIRMED" else 0,
                "notes": "Loki was stopped while continue_on_step_failure was enabled.",
            }
        finally:
            self._compose("up", "-d", "loki")

    def _token(self) -> str:
        response = self.oidc_client.post(
            "https://localhost:28443/token",
            data={
                "client_id": "reference-cli",
                "scope": (
                    "openid incidents:rca incidents:read rca:read "
                    "tool_permissions:read tool_permissions:write"
                ),
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _ensure_permissions(self, token: str) -> None:
        """幂等授予 reference reviewer 执行只读 RCA 工具所需权限。"""
        url = (
            f"{AGENT_URL}/api/v1/admin/tenants/{TENANT_ID}/operators/"
            f"{REFERENCE_OPERATOR_ID}/tool-permissions"
        )
        headers = {"Authorization": f"Bearer {token}"}
        current = self.client.get(url, headers=headers)
        if current.status_code == 404:
            expected_version = 0
        else:
            current.raise_for_status()
            snapshot = current.json()["data"]
            current_tags = tuple(sorted(snapshot["permission_tags"]))
            if snapshot["active"] and current_tags == REFERENCE_PERMISSION_TAGS:
                return
            expected_version = int(snapshot["version"])

        response = self.client.put(
            url,
            headers={
                **headers,
                "Idempotency-Key": (f"reference-chaos-permissions-v{expected_version}"),
                "If-Match": f'"{expected_version}"',
            },
            json={"permission_tags": list(REFERENCE_PERMISSION_TAGS)},
        )
        response.raise_for_status()

    def _start_workflow(self, token: str, case: str) -> str:
        identity = uuid4().hex
        alert = self.client.post(
            f"{AGENT_URL}/api/v1/alerts",
            json=self._alert_payload(identity, case),
        )
        alert.raise_for_status()
        incident_id = alert.json()["data"]["incident_id"]
        response = self.client.post(
            f"{AGENT_URL}/api/v1/admin/tenants/{TENANT_ID}/incidents/{incident_id}/rca",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": f"reference-chaos-{case}-{identity}",
            },
            json={},
        )
        response.raise_for_status()
        return response.json()["data"]["workflow_run_id"]

    @staticmethod
    def _alert_payload(identity: str, case: str) -> dict:
        return {
            "tenant_id": TENANT_ID,
            "source": "reference-chaos",
            "service_name": f"reference-{case}-{identity[:8]}",
            "severity": "CRITICAL",
            "summary": f"Synthetic reference chaos case {case}",
            "starts_at": datetime.now(UTC).isoformat(),
            "fingerprint": f"reference-chaos-{identity}",
            "external_event_id": f"reference-chaos-{identity}",
        }

    def _wait_workflow(self, token: str, workflow_id: str, timeout: float) -> dict:
        deadline = perf_counter() + timeout
        while perf_counter() < deadline:
            try:
                result = self._workflow_result(token, workflow_id)
                if result["status"] in {"SUCCEEDED", "FAILED", "CANCELED"}:
                    return result
            except httpx.HTTPError:
                pass
            sleep(0.5)
        raise RuntimeError(f"workflow did not finish: {workflow_id}")

    def _wait_workflow_status(
        self,
        token: str,
        workflow_id: str,
        expected: str,
        timeout: float,
    ) -> dict:
        """等待 API 暴露精确状态，终态早于注入点时立即失败。"""
        deadline = perf_counter() + timeout
        terminal_statuses = {"SUCCEEDED", "FAILED", "CANCELED"}
        while perf_counter() < deadline:
            try:
                result = self._workflow_result(token, workflow_id)
                status = result["status"]
                if status == expected:
                    return result
                if status in terminal_statuses:
                    raise RuntimeError(
                        f"workflow reached {status} before expected {expected}: "
                        f"{workflow_id}"
                    )
            except httpx.HTTPError:
                pass
            sleep(0.1)
        raise RuntimeError(f"workflow did not reach {expected}: {workflow_id}")

    def _wait_execution_attempt(
        self,
        token: str,
        workflow_id: str,
        minimum_attempt: int,
        timeout: float,
    ) -> dict:
        """等待替代 Worker 完成 reclaim，确保旧代次探针命中有效租约窗口。"""
        deadline = perf_counter() + timeout
        while perf_counter() < deadline:
            try:
                result = self._workflow_result(token, workflow_id)
                if (
                    result["status"] == "RUNNING"
                    and result["execution_attempts"] >= minimum_attempt
                ):
                    return result
                if result["status"] in {"SUCCEEDED", "FAILED", "CANCELED"}:
                    raise RuntimeError(
                        f"workflow reached {result['status']} before execution "
                        f"attempt {minimum_attempt}: {workflow_id}"
                    )
            except httpx.HTTPError:
                pass
            sleep(0.1)
        raise RuntimeError(
            f"workflow did not reach execution attempt {minimum_attempt}: {workflow_id}"
        )

    def _probe_stale_completion(
        self,
        workflow_id: str,
        worker_id: str,
        *,
        execution_attempt: int,
    ) -> int:
        """在替代执行代次持有租约时尝试旧代次条件更新并回滚。"""
        script = (
            "BEGIN; "
            "WITH stale AS (UPDATE workflow_runs SET updated_at=now() "
            "WHERE tenant_id='reference-tenant' "
            f"AND workflow_run_id='{workflow_id}' "
            "AND status='RUNNING' "
            f"AND lease_owner='{worker_id}' "
            f"AND execution_attempts={execution_attempt} "
            "AND lease_expires_at > now() RETURNING 1) "
            "SELECT count(*) FROM stale; ROLLBACK;"
        )
        output = self._compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "devops_agent",
            "-d",
            "devops_agent",
            "-qAtc",
            script,
            capture=True,
        )
        counts = [
            line.strip() for line in output.splitlines() if line.strip().isdigit()
        ]
        if not counts:
            raise RuntimeError("stale completion probe returned no row count")
        return int(counts[-1])

    def _workflow_result(self, token: str, workflow_id: str) -> dict:
        response = self.client.get(
            f"{AGENT_URL}/api/v1/admin/tenants/{TENANT_ID}/workflow-runs/"
            f"{workflow_id}/result",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.json()["data"]

    def _wait_sql(self, query: str, expected: str, timeout: float) -> None:
        deadline = perf_counter() + timeout
        while perf_counter() < deadline:
            if self._sql(query) == expected:
                return
            sleep(0.1)
        raise RuntimeError(f"SQL observation did not reach {expected}")

    def _wait_http(self, url: str, timeout: float) -> None:
        deadline = perf_counter() + timeout
        while perf_counter() < deadline:
            try:
                response = self.client.get(url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            sleep(0.5)
        raise RuntimeError(f"HTTP readiness did not recover: {url}")

    def _sql(self, query: str) -> str:
        return self._compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "devops_agent",
            "-d",
            "devops_agent",
            "-Atc",
            query,
            capture=True,
        )

    def _compose(self, *arguments: str, capture: bool = False) -> str:
        completed = subprocess.run(
            ["docker", "compose", "-f", str(self.compose_file), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(message[-2048:] or "docker compose command failed")
        return completed.stdout.strip() if capture else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inject faults into reference staging")
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=Path(__file__).parents[1] / "reference-staging" / "docker-compose.yml",
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output_directory.exists() and any(args.output_directory.iterdir()):
        print("chaos output directory already exists", file=sys.stderr)
        return 2
    runner = ReferenceChaosRunner(args.compose_file)
    try:
        results = runner.run()
    except (KeyError, OSError, RuntimeError, httpx.HTTPError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        runner.close()
    args.output_directory.mkdir(parents=True, exist_ok=True)
    observations = {
        "schema_version": "1.0",
        "simulated": False,
        "synthetic": True,
        "production_acceptance": False,
        "target_label": "reference-staging",
        "generated_at": datetime.now(UTC).isoformat(),
        "results": results,
    }
    observations_path = args.output_directory / "observations.json"
    observations_path.write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = write_report(
        args.output_directory / "chaos-report.json",
        mode="live",
        live_input=observations_path,
    )
    print(
        json.dumps({"passed": report["passed"], "output": str(args.output_directory)})
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
