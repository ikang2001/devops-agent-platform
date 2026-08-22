"""Public-scenario MiniShop executor for the v0.7 black-box runner.

The executor never imports or reads a Private Ground Truth document. It only turns
the terminal platform result into a runtime snapshot for the generic black-box
benchmark runner.
"""

# The MiniShop app is loaded from its scenario directory before importing its
# helper module; those imports intentionally occur after sys.path setup.
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MINISHOP_ROOT = _PROJECT_ROOT / "MiniShop 电商下单故障演练靶场"
if str(_MINISHOP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MINISHOP_ROOT))

from run_e2e import E2EConfig, E2EFailure, JsonHttpClient, wait_for_value  # noqa: E402

from devops_agent_platform.evaluation.blackbox import BlackBoxExecution  # noqa: E402
from devops_agent_platform.evaluation.live_runner import (  # noqa: E402
    LiveEvidence,
    LiveScenarioInput,
)
from devops_agent_platform.evaluation.scenario_catalog import (
    PublicScenario,  # noqa: E402
)
from devops_agent_platform.evaluation.schemas import (
    CausalEdge,
    Claim,
    ClaimType,
    ConclusionStatus,
    EvidenceType,
    RCAPrediction,
    RootCauseCandidateRef,
    RootCauseRef,
    ToolCall,
    ToolStatus,
)


class MiniShopBlackBoxExecutor:
    """Execute public MiniShop actions and collect only live runtime evidence."""

    produces_terminal_prediction = True

    def __init__(self, config: E2EConfig) -> None:
        self.config = config
        self.http = JsonHttpClient()
        self._prepared = False

    def close(self) -> None:
        self.http.close()

    async def execute(self, scenario: PublicScenario) -> LiveScenarioInput:
        return await asyncio.to_thread(self._execute_sync, scenario)

    def _execute_sync(self, scenario: PublicScenario) -> LiveScenarioInput:
        self._prepare_once()
        started_at = datetime.now(UTC)
        incident: dict[str, Any] | None = None
        try:
            self._invoke_action(scenario.cleanup)
            if scenario.fault_type in {"deployment_regression", "config_regression"}:
                self._record_change_event(scenario, started_at)
            self._invoke_action(scenario.injection)
            self._trigger(scenario, started_at)
            incident = self._wait_for_incident(scenario, started_at)
            workflow_run_id = self._start_rca(incident["incident_id"], scenario)
            result = self._wait_for_workflow(workflow_run_id)
            runtime = self._runtime_snapshot(scenario, incident, result)
            prediction = self._prediction(scenario, result)
            return BlackBoxExecution(runtime=runtime, prediction=prediction)
        finally:
            self._invoke_action(scenario.cleanup)
            if incident is not None:
                self._wait_for_alert_clear(scenario)
                self._settle_matching_incidents(
                    scenario,
                    started_at=started_at,
                    primary_incident_id=str(incident["incident_id"]),
                )

    def _prepare_once(self) -> None:
        if self._prepared:
            return
        self._wait_for_stack()
        self._grant_tool_permissions()
        self._prepared = True

    def _wait_for_stack(self) -> None:
        health_urls = {
            "agent": f"{self.config.agent_url}/readyz",
            "minishop": f"{self.config.minishop_url}/healthz",
            "prometheus": f"{self.config.prometheus_url}/-/ready",
            "loki": f"{self.config.loki_url}/ready",
            "tempo": f"{self.config.tempo_url}/ready",
            "alertmanager": f"{self.config.alertmanager_url}/-/ready",
        }
        for name, url in health_urls.items():
            result = wait_for_value(
                name,
                lambda target=url: self._health(target),
                timeout_seconds=self.config.timeout_seconds,
                interval_seconds=2,
            )
            if result is None:
                raise E2EFailure(f"{name} did not become ready")

    def _health(self, url: str) -> dict[str, bool] | None:
        try:
            response = self.http.request("GET", url)
        except Exception:  # noqa: BLE001 - network boundary is intentionally bounded
            return None
        return {} if not 200 <= response.status_code < 300 else {"ready": True}

    def _grant_tool_permissions(self) -> None:
        url = self._admin_url(f"operators/{self.config.admin_id}/tool-permissions")
        current = self.http.request("GET", url, headers=self._auth_headers())
        if current.status_code != 200:
            raise E2EFailure("tool permission read failed")
        etag = current.headers.get("ETag")
        if etag is None:
            raise E2EFailure("tool permission response did not include ETag")
        response = self.http.request(
            "PUT",
            url,
            headers=self._write_headers(
                f"blackbox-permissions-{uuid.uuid4().hex}", etag
            ),
            json={
                "permission_tags": [
                    "logs:read",
                    "changes:read",
                    "metrics:read",
                    "runbooks:read",
                    "tenant:observe",
                    "traces:read",
                    "topology:read",
                    "knowledge:read",
                ]
            },
        )
        self._require_platform_success(response, "grant tool permissions")

    def _record_change_event(
        self,
        scenario: PublicScenario,
        started_at: datetime,
    ) -> None:
        service = scenario.service_name
        payload = {
            "tenant_id": self.config.tenant_id,
            "source": "blackbox-scenario-runner",
            "external_event_id": (
                f"blackbox-change-{scenario.scenario_id}-{uuid.uuid4().hex}"
            ),
            "service_name": service,
            "resource_type": "deployment",
            "resource_id": service,
            "change_type": "DEPLOYMENT",
            "status": "SUCCEEDED",
            "version_before": "v1",
            "version_after": "v2",
            "operator_id": "blackbox-runner",
            "summary": f"{service} deployment version changed",
            "metadata": {"environment": "minishop-blackbox"},
            "started_at": started_at.isoformat(),
            "completed_at": started_at.isoformat(),
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.config.webhook_secret.encode(),
            timestamp.encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        response = self.http.request(
            "POST",
            f"{self.config.agent_url}/api/v1/change-events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-DevOps-Agent-Timestamp": timestamp,
                "X-DevOps-Agent-Signature": f"sha256={signature}",
            },
        )
        self._require_platform_success(response, "record public change event")

    def _invoke_action(self, action: dict[str, Any]) -> dict[str, Any]:
        method = action.get("method")
        path = action.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            raise E2EFailure("public scenario action is invalid")
        response = self.http.request(
            method,
            f"{self.config.minishop_url}{path}",
            json=action.get("json_body", {}),
        )
        expected = action.get("expected_status")
        if response.status_code != expected:
            raise E2EFailure(
                f"{method} {path} expected {expected}, got {response.status_code}"
            )
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _trigger(self, scenario: PublicScenario, started_at: datetime) -> None:
        del started_at
        action = scenario.trigger
        method = action.get("method")
        path = action.get("path")
        expected = action.get("expected_status")
        if not isinstance(method, str) or not isinstance(path, str):
            raise E2EFailure("public trigger is invalid")
        response = self.http.request(
            method,
            f"{self.config.minishop_url}{path}",
            json=action.get("json_body", {}),
            headers={"X-Trace-Id": f"blackbox-{uuid.uuid4().hex}"},
        )
        if response.status_code != expected:
            raise E2EFailure(
                f"{method} {path} expected {expected}, got {response.status_code}"
            )

    def _wait_for_incident(
        self,
        scenario: PublicScenario,
        started_at: datetime,
    ) -> dict[str, Any]:
        alert = scenario.alert_mapping
        title = alert.get("summary")

        def find() -> dict[str, Any] | None:
            response = self.http.request(
                "GET",
                self._admin_url("incidents"),
                headers=self._auth_headers(),
                params={"limit": "100"},
            )
            data = self._require_platform_success(response, "list black-box incidents")
            return self._select_incident(
                data.get("items", []),
                scenario=scenario,
                expected_title=title,
                started_at=started_at,
            )

        result = wait_for_value(
            f"incident for {scenario.scenario_id}",
            find,
            timeout_seconds=self.config.timeout_seconds,
            interval_seconds=2,
        )
        if result is None:
            raise E2EFailure(f"incident was not created for {scenario.scenario_id}")
        return result

    @staticmethod
    def _select_incident(
        items: object,
        *,
        scenario: PublicScenario,
        expected_title: object,
        started_at: datetime,
    ) -> dict[str, Any] | None:
        """Select the incident created by this isolated scenario execution.

        One fault can fire multiple alert rules for the same service. The platform
        intentionally correlates those alerts into one incident, whose title is
        taken from the first alert and can therefore differ from the scenario's
        primary alert summary. Prefer the exact title, then accept the newest
        same-service incident created inside this execution window.
        """

        if not isinstance(items, list):
            return None
        candidates: list[tuple[datetime, dict[str, Any]]] = []
        for incident in items:
            if not isinstance(incident, dict):
                continue
            created_at_value = incident.get("created_at")
            if not isinstance(created_at_value, str):
                continue
            try:
                created_at = datetime.fromisoformat(created_at_value)
            except ValueError:
                continue
            if (
                incident.get("service_name") != scenario.service_name
                or incident.get("status") != "OPEN"
                or created_at < started_at - timedelta(seconds=10)
            ):
                continue
            if incident.get("title") == expected_title:
                return incident
            candidates.append((created_at, incident))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _wait_for_alert_clear(self, scenario: PublicScenario) -> None:
        """Require two stable Prometheus evaluations before a scenario rerun."""

        alert_name = str(scenario.alert_mapping.get("alert_name", ""))
        if not alert_name:
            return

        inactive_since: float | None = None

        def clear() -> dict[str, bool] | None:
            nonlocal inactive_since
            try:
                response = self.http.request(
                    "GET",
                    f"{self.config.prometheus_url}/api/v1/rules",
                )
            except Exception:  # noqa: BLE001 - bounded network poll
                return None
            if response.status_code != 200:
                return None
            document = response.json()
            groups = document.get("data", {}).get("groups", [])
            if not isinstance(groups, list):
                return None
            rules = [
                rule
                for group in groups
                if isinstance(group, dict)
                for rule in group.get("rules", [])
                if isinstance(rule, dict) and rule.get("name") == alert_name
            ]
            if len(rules) != 1 or rules[0].get("state") != "inactive":
                inactive_since = None
                return None
            now = time.monotonic()
            inactive_since = inactive_since or now
            return {"clear": True} if now - inactive_since >= 4 else None

        result = wait_for_value(
            f"alert clear for {scenario.scenario_id}",
            clear,
            timeout_seconds=self.config.timeout_seconds,
            interval_seconds=2,
        )
        if result is None:
            raise E2EFailure(f"alert did not clear for {scenario.scenario_id}")

    def _settle_matching_incidents(
        self,
        scenario: PublicScenario,
        *,
        started_at: datetime,
        primary_incident_id: str,
    ) -> None:
        """Close the primary and any late duplicate incident for the same run."""

        response = self.http.request(
            "GET",
            self._admin_url("incidents"),
            headers=self._auth_headers(),
            params={"limit": "100"},
        )
        data = self._require_platform_success(
            response, "list black-box incidents before settlement"
        )
        candidates = {primary_incident_id}
        for item in data.get("items", []):
            if not isinstance(item, dict):
                continue
            created_at = datetime.fromisoformat(str(item["created_at"]))
            if (
                item.get("service_name") == scenario.service_name
                and item.get("status") in {"OPEN", "ANALYZING"}
                and created_at >= started_at - timedelta(seconds=10)
            ):
                # One injected fault can emit multiple alert titles that are
                # either correlated or delivered late. They all belong to this
                # isolated service/time window and must be settled together.
                candidates.add(str(item["incident_id"]))
        for incident_id in sorted(candidates):
            self._settle_incident(incident_id)

    def _start_rca(self, incident_id: str, scenario: PublicScenario) -> str:
        response = self.http.request(
            "POST",
            self._admin_url(f"incidents/{incident_id}/rca"),
            headers={
                **self._auth_headers(),
                "Idempotency-Key": (
                    f"blackbox-rca-{scenario.scenario_id}-{uuid.uuid4().hex}"
                ),
            },
            json={},
        )
        return self._require_platform_success(response, "start black-box RCA")[
            "workflow_run_id"
        ]

    def _wait_for_workflow(self, workflow_run_id: str) -> dict[str, Any]:
        def terminal() -> dict[str, Any] | None:
            response = self.http.request(
                "GET",
                self._admin_url(f"workflow-runs/{workflow_run_id}/result"),
                headers=self._auth_headers(),
            )
            data = self._require_platform_success(response, "read black-box RCA")
            status = data.get("status")
            if status in {"FAILED", "CANCELED"}:
                return data
            if status != "SUCCEEDED":
                return None
            # Workflow 状态和结果投影由不同事务可见，终态刚落库时接口可能
            # 短暂返回空 Evidence/Report。继续轮询完整终态，避免把投影延迟
            # 误判成真实的“无证据”实验结果。
            if not data.get("evidence") or not isinstance(data.get("report"), dict):
                return None
            return data

        result = wait_for_value(
            f"workflow {workflow_run_id}",
            terminal,
            timeout_seconds=self.config.timeout_seconds,
            interval_seconds=2,
        )
        if result is None or result.get("status") != "SUCCEEDED":
            raise E2EFailure(f"workflow {workflow_run_id} did not succeed")
        return result

    def _settle_incident(self, incident_id: str) -> None:
        """Resolve and close a completed benchmark incident before the next run."""

        base = self._admin_url(f"incidents/{incident_id}")
        current = self.http.request("GET", base, headers=self._auth_headers())
        current_data = self._require_platform_success(
            current, "read black-box incident before resolution"
        )
        if current_data.get("status") not in {"OPEN", "ANALYZING"}:
            return
        etag = current.headers.get("ETag")
        if etag is None:
            raise E2EFailure("black-box incident response did not include ETag")
        resolved = self.http.request(
            "POST",
            f"{base}/resolution",
            headers=self._write_headers(
                f"blackbox-resolve-{incident_id}-{uuid.uuid4().hex}", etag
            ),
            json={"reason": "MiniShop black-box scenario completed"},
        )
        self._require_platform_success(resolved, "resolve black-box incident")
        resolved_etag = resolved.headers.get("ETag")
        if resolved_etag is None:
            raise E2EFailure("black-box incident resolution did not include ETag")
        closed = self.http.request(
            "POST",
            f"{base}/closure",
            headers=self._write_headers(
                f"blackbox-close-{incident_id}-{uuid.uuid4().hex}", resolved_etag
            ),
            json={"reason": "MiniShop black-box scenario artifacts captured"},
        )
        self._require_platform_success(closed, "close black-box incident")

    def _runtime_snapshot(
        self,
        scenario: PublicScenario,
        incident: dict[str, Any],
        result: dict[str, Any],
    ) -> LiveScenarioInput:
        evidence = tuple(self._evidence(item) for item in result.get("evidence", []))
        tool_calls = tuple(
            self._tool_call(item) for item in result.get("invocations", [])
        )
        if not evidence:
            raise E2EFailure(
                f"black-box RCA returned no evidence for {scenario.scenario_id}"
            )
        return LiveScenarioInput(
            scenario_id=scenario.scenario_id,
            incident_id=str(incident["incident_id"]),
            service_name=scenario.service_name,
            entry_service=self._entry_service(scenario),
            summary=str(scenario.alert_mapping.get("summary", "runtime incident")),
            evidence=evidence,
            tool_calls=tool_calls,
            investigation_steps=max(
                len(tool_calls), int(result.get("investigation_steps", 0))
            ),
        )

    def _prediction(
        self,
        scenario: PublicScenario,
        result: dict[str, Any],
    ) -> RCAPrediction:
        report = result.get("report")
        if not isinstance(report, dict):
            raise E2EFailure("RCA result did not include a structured report")
        try:
            status = ConclusionStatus(str(report["conclusion_status"]))
            evidence_ids = tuple(str(item) for item in report["evidence_ids"])
            confidence = float(report["confidence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise E2EFailure("RCA report fields are invalid") from exc
        root_payload = report.get("root_cause")
        root = (
            RootCauseRef.model_validate(root_payload)
            if isinstance(root_payload, dict)
            else None
        )
        claims = (
            (
                Claim(
                    claim_type=ClaimType.ROOT_CAUSE,
                    statement=str(report.get("summary", "RCA candidate")),
                    evidence_ids=evidence_ids,
                ),
            )
            if root is not None
            else ()
        )
        evidence = tuple(
            item for item in result.get("evidence", []) if isinstance(item, dict)
        )
        evidence_types = tuple(
            sorted(
                {
                    self._map_evidence_type(str(item["evidence_type"]))
                    for item in evidence
                    if str(item.get("evidence_id")) in evidence_ids
                },
                key=lambda item: item.value,
            )
        )
        invocations = tuple(
            item for item in result.get("invocations", []) if isinstance(item, dict)
        )
        tool_calls = tuple(
            self._terminal_tool_call(item, evidence) for item in invocations
        )
        candidates = tuple(
            self._candidate(item) for item in report.get("root_cause_candidates", [])
        )
        return RCAPrediction(
            scenario_id=scenario.scenario_id,
            incident_id=str(result["incident_id"]),
            run_id=str(result["workflow_run_id"]),
            root_cause=root,
            conclusion_status=status,
            confidence=confidence,
            evidence_ids=evidence_ids,
            evidence_types=evidence_types,
            claims=claims,
            causal_chain=tuple(
                CausalEdge.model_validate(item)
                for item in report.get("causal_chain", [])
            ),
            affected_services=tuple(
                str(item) for item in report.get("affected_services", [])
            ),
            root_cause_candidates=candidates,
            tool_calls=tool_calls,
            investigation_steps=max(int(result.get("step_count", 0)), len(tool_calls)),
            llm_calls=(
                1 if report.get("generator_name") == "llm-structured-report" else 0
            ),
            latency_ms=sum(int(item.get("latency_ms", 0)) for item in invocations),
            total_tokens=0,
            estimated_cost=0.0,
        )

    @staticmethod
    def _terminal_tool_call(
        invocation: dict[str, Any],
        evidence: tuple[dict[str, Any], ...],
    ) -> ToolCall:
        tool_name = str(invocation.get("tool_name", "unknown"))
        tool_version = str(invocation.get("tool_version", "v1"))
        evidence_ids = tuple(
            str(item["evidence_id"])
            for item in evidence
            if item.get("step_id") == invocation.get("step_id")
            and item.get("tool_name") == invocation.get("tool_name")
        )
        try:
            status = ToolStatus(str(invocation.get("status", "FAILED")))
        except ValueError:
            status = ToolStatus.FAILED
        return ToolCall(
            tool_type=(
                tool_name if "@" in tool_name else f"{tool_name}@{tool_version}"
            ),
            status=status,
            evidence_ids=evidence_ids,
        )

    @classmethod
    def _candidate(cls, item: object) -> RootCauseCandidateRef:
        if not isinstance(item, dict):
            raise E2EFailure("RCA candidate item is invalid")
        candidate = dict(item)
        if "root_cause" not in candidate:
            try:
                candidate["root_cause"] = {
                    "service": candidate.pop("service"),
                    "type": candidate.pop("root_type"),
                    "resource": candidate.pop("resource", None),
                }
            except KeyError as exc:
                raise E2EFailure("RCA candidate item is invalid") from exc
        source_types = candidate.get("source_evidence_types", [])
        candidate["source_evidence_types"] = [
            cls._map_evidence_type(str(value)) for value in source_types
        ]
        try:
            return RootCauseCandidateRef.model_validate(candidate)
        except ValueError as exc:
            raise E2EFailure("RCA candidate item is invalid") from exc

    @staticmethod
    def _evidence(item: object) -> LiveEvidence:
        if not isinstance(item, dict):
            raise E2EFailure("RCA evidence item is invalid")
        try:
            evidence_type = MiniShopBlackBoxExecutor._map_evidence_type(
                str(item["evidence_type"])
            )
            evidence_id = str(item["evidence_id"])
            source = str(item.get("tool_name", item.get("source", "runtime")))
            summary = str(item["summary"])
        except (KeyError, TypeError, ValueError) as exc:
            raise E2EFailure("RCA evidence item is incomplete") from exc
        return LiveEvidence(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            source=source,
            summary=summary,
        )

    @staticmethod
    def _map_evidence_type(value: str) -> EvidenceType:
        """把平台领域 Evidence 类型转换为评测契约类型。"""

        aliases = {
            "DEPLOYMENT": EvidenceType.CHANGE,
            "INCIDENT_HISTORY": EvidenceType.KNOWLEDGE,
        }
        if value in aliases:
            return aliases[value]
        try:
            return EvidenceType(value)
        except ValueError as exc:
            raise E2EFailure(f"unsupported platform evidence type: {value}") from exc

    @staticmethod
    def _tool_call(item: object) -> ToolCall:
        if not isinstance(item, dict):
            raise E2EFailure("RCA invocation item is invalid")
        status_text = str(item.get("status", "FAILED"))
        try:
            status = ToolStatus(status_text)
        except ValueError:
            status = ToolStatus.FAILED
        return ToolCall(
            tool_type=str(item.get("tool_type", item.get("tool_name", "unknown@v1"))),
            status=status,
            evidence_ids=tuple(str(value) for value in item.get("evidence_ids", [])),
            proposal_valid=bool(item.get("proposal_valid", True)),
        )

    @staticmethod
    def _entry_service(scenario: PublicScenario) -> str | None:
        path = scenario.trigger.get("path")
        if not isinstance(path, str):
            return None
        segments = path.strip("/").split("/")
        if not segments or not segments[0]:
            return None
        if segments[0] == "holdout" and len(segments) >= 2:
            return segments[1]
        return f"{segments[0]}-service"

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
        response: Any,
        operation: str,
    ) -> dict[str, Any]:
        if not 200 <= response.status_code < 300:
            raise E2EFailure(f"{operation} returned HTTP {response.status_code}")
        document = response.json()
        if not isinstance(document, dict) or document.get("success") is not True:
            raise E2EFailure(f"{operation} returned invalid platform envelope")
        data = document.get("data")
        if not isinstance(data, dict):
            raise E2EFailure(f"{operation} returned invalid platform data")
        return data
