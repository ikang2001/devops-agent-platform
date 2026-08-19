from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.investigation import (
    InvestigationState,
    InvestigationStopReason,
    StepDecision,
)
from devops_agent_platform.domain.models.topology import TopologyGraph
from devops_agent_platform.ports.investigation import InvestigationCheckpointPort
from devops_agent_platform.ports.investigation_codec import (
    deserialize_investigation_state as _deserialize_investigation_state,
)
from devops_agent_platform.ports.investigation_codec import (
    serialize_investigation_state as _serialize_investigation_state,
)
from devops_agent_platform.tools.registry import ToolRegistry


def serialize_investigation_state(state: InvestigationState) -> str:
    return _serialize_investigation_state(state)


def deserialize_investigation_state(value: str) -> InvestigationState:
    return _deserialize_investigation_state(value)


class IntentPlannerPort(Protocol):
    async def next_step(self, state: InvestigationState) -> StepDecision: ...


@dataclass(frozen=True)
class PolicyValidationResult:
    allowed: bool
    reason_code: str
    normalized_payload: dict[str, Any]


class InvestigationPolicyValidator:
    """后端验证模型意图，模型永远不能直接提交最终 Tool Payload。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        allowed_tools: frozenset[str] | None = None,
        read_only_only: bool = True,
    ) -> None:
        self._registry = registry
        self._allowed_tools = allowed_tools or frozenset(
            {
                "metrics.query@v1",
                "logs.query@v1",
                "traces.query@v1",
                "changes.query@v1",
                "topology.query@v1",
                "knowledge.search@v1",
                "runbooks.retrieve@v1",
            }
        )
        self._read_only_only = read_only_only

    def validate(
        self,
        state: InvestigationState,
        decision: StepDecision,
        *,
        topology: TopologyGraph | None = None,
        operator_id: str | None = "agent",
    ) -> PolicyValidationResult:
        if decision.stop:
            return PolicyValidationResult(True, "MODEL_REQUESTED_STOP", {})
        if decision.next_tool is None or "@" not in decision.next_tool:
            return PolicyValidationResult(False, "UNKNOWN_TOOL", {})
        if decision.next_tool not in self._allowed_tools:
            return PolicyValidationResult(False, "TOOL_NOT_ALLOWLISTED", {})
        tool_name, version = decision.next_tool.rsplit("@", 1)
        try:
            definition = self._registry.get(tool_name, version)
        except Exception:
            return PolicyValidationResult(False, "TOOL_NOT_REGISTERED", {})
        if self._read_only_only and definition.risk_level is not ToolRiskLevel.LOW:
            return PolicyValidationResult(False, "WRITE_OR_HIGH_RISK_TOOL", {})
        if operator_id is None:
            return PolicyValidationResult(False, "OPERATOR_NOT_AUTHORIZED", {})
        if (
            state.tool_call_counts.get(decision.next_tool, 0)
            >= state.remaining_budget.max_tool_calls_per_type
        ):
            return PolicyValidationResult(False, "TOOL_CALL_BUDGET_EXHAUSTED", {})
        if (
            decision.next_tool in state.visited_tools
            and not decision.required_evidence_types
        ):
            return PolicyValidationResult(False, "DUPLICATE_TOOL_CALL", {})
        if decision.target_service is not None and topology is not None:
            service_names = {
                getattr(node, "service_name", "") for node in topology.nodes
            }
            if decision.target_service not in service_names:
                return PolicyValidationResult(False, "SERVICE_OUTSIDE_TOPOLOGY", {})
        try:
            payload = self._build_payload(state, decision)
        except AppValidationError:
            return PolicyValidationResult(False, "INVALID_TOOL_PAYLOAD", {})
        return PolicyValidationResult(True, "ALLOWED", payload)

    @staticmethod
    def _build_payload(
        state: InvestigationState, decision: StepDecision
    ) -> dict[str, Any]:
        """只生成各只读工具认可的有限参数，拒绝模型自带查询语句。"""
        if decision.next_tool is None:
            raise AppValidationError("dynamic payload requires a tool")
        tool_name = decision.next_tool.rsplit("@", 1)[0]
        service = decision.target_service or state.service_name
        payload: dict[str, Any] = {
            "tenant_id": state.tenant_id,
            "incident_id": state.incident_id,
        }
        if tool_name == "metrics.query":
            payload.update(
                window_minutes=15,
                max_series=40,
                signals=[
                    "availability",
                    "request_rate",
                    "error_ratio",
                    "latency_p95",
                ],
            )
        elif tool_name == "logs.query":
            payload.update(
                window_minutes=15,
                limit=30,
                signals=["errors", "timeouts", "resource_pressure"],
            )
        elif tool_name == "traces.query":
            payload.update(
                window_minutes=15,
                limit=12,
                signals=["errors", "slow_spans"],
            )
        elif tool_name == "changes.query":
            payload["max_results"] = 20
        elif tool_name == "runbooks.retrieve":
            payload["max_results"] = 5
        elif tool_name == "topology.query":
            payload.update(max_depth=8, environment=state.environment)
        elif tool_name == "knowledge.search":
            if service is None:
                raise AppValidationError("service is required for knowledge search")
            payload.update(
                service=service,
                alert_summary=state.alert_summary,
                error_fingerprint=state.error_fingerprint,
                log_keywords=list(state.log_keywords),
                trace_errors=list(state.trace_errors),
                recent_change_type=state.recent_change_type,
                top_k=5,
            )
        else:
            raise AppValidationError(f"unsupported dynamic tool: {tool_name}")
        return payload


class BoundedDynamicInvestigator:
    def __init__(
        self,
        validator: InvestigationPolicyValidator,
        *,
        now: callable | None = None,
        checkpoint: InvestigationCheckpointPort | None = None,
    ) -> None:
        self._validator = validator
        self._now = now or (lambda: datetime.now().astimezone())
        self._checkpoint = checkpoint

    async def run(
        self,
        state: InvestigationState,
        planner: IntentPlannerPort,
        *,
        execute_tool: Any,
        topology: TopologyGraph | None = None,
    ) -> InvestigationState:
        while state.can_continue(self._now()):
            try:
                decision = await planner.next_step(state)
            except Exception:
                # Planner 故障不能被当作“证据充分”；保留已收集证据并
                # 以失败停止，交给上层报告生成器决定是否输出 partial RCA。
                state.mark_stop(InvestigationStopReason.FAILED)
                if self._checkpoint is not None:
                    await self._checkpoint.save(state)
                break
            if not isinstance(decision, StepDecision):
                state.mark_stop(InvestigationStopReason.POLICY_BLOCKED)
                if self._checkpoint is not None:
                    await self._checkpoint.save(state)
                break
            validation = self._validator.validate(state, decision, topology=topology)
            if not validation.allowed:
                if decision.next_tool is not None:
                    state.record_tool(decision.next_tool, succeeded=False)
                state.mark_stop(InvestigationStopReason.POLICY_BLOCKED)
                if self._checkpoint is not None:
                    await self._checkpoint.save(state)
                break
            if decision.stop:
                state.mark_stop(InvestigationStopReason.EVIDENCE_SUFFICIENT)
                break
            try:
                result = await execute_tool(
                    decision.next_tool, validation.normalized_payload
                )
                evidence_ids = (
                    tuple(result.get("evidence_ids", ()))
                    if isinstance(result, dict)
                    else ()
                )
                state.record_tool(
                    decision.next_tool or "", succeeded=True, evidence_ids=evidence_ids
                )
                if (
                    state.step_count >= state.remaining_budget.max_steps
                ):
                    state.mark_stop(
                        InvestigationStopReason.NO_ACTIONABLE_ROOT_CAUSE
                        if not evidence_ids
                        else InvestigationStopReason.EVIDENCE_SUFFICIENT
                    )
                if self._checkpoint is not None:
                    await self._checkpoint.save(state)
            except Exception:
                state.record_tool(decision.next_tool or "", succeeded=False)
                if self._checkpoint is not None:
                    await self._checkpoint.save(state)
        if state.stop_reason is None:
            state.mark_stop(InvestigationStopReason.BUDGET_EXHAUSTED)
        return state

    async def resume(
        self,
        tenant_id: str,
        incident_id: str,
        planner: IntentPlannerPort,
        *,
        execute_tool: Any,
        topology: TopologyGraph | None = None,
    ) -> InvestigationState | None:
        if self._checkpoint is None:
            raise AppValidationError("checkpoint storage is required for resume")
        state = await self._checkpoint.load(tenant_id, incident_id)
        if state is None:
            return None
        return await self.run(
            state,
            planner,
            execute_tool=execute_tool,
            topology=topology,
        )
