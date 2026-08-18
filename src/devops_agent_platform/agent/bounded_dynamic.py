from __future__ import annotations

import json
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
from devops_agent_platform.tools.registry import ToolRegistry


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
        payload = self._build_payload(state, decision)
        return PolicyValidationResult(True, "ALLOWED", payload)

    @staticmethod
    def _build_payload(
        state: InvestigationState, decision: StepDecision
    ) -> dict[str, Any]:
        # 只从服务端状态和固定边界生成参数；intent 中的任意 payload 字段都会被忽略。
        payload: dict[str, Any] = {
            "tenant_id": state.tenant_id,
            "incident_id": state.incident_id,
            "max_results": 20,
            "window_minutes": 15,
        }
        if decision.target_service is not None:
            payload["service_name"] = decision.target_service
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
            decision = await planner.next_step(state)
            if not isinstance(decision, StepDecision):
                raise AppValidationError("planner must return StepDecision")
            validation = self._validator.validate(state, decision, topology=topology)
            if not validation.allowed:
                state.mark_stop(InvestigationStopReason.POLICY_BLOCKED)
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
                    not evidence_ids
                    and state.step_count >= state.remaining_budget.max_steps
                ):
                    state.mark_stop(InvestigationStopReason.NO_ACTIONABLE_ROOT_CAUSE)
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


def serialize_investigation_state(state: InvestigationState) -> str:
    return json.dumps(
        {
            "incident_id": state.incident_id,
            "tenant_id": state.tenant_id,
            "completed_steps": state.completed_steps,
            "failed_steps": state.failed_steps,
            "evidence_ids": state.evidence_ids,
            "observed_signals": state.observed_signals,
            "candidate_root_services": state.candidate_root_services,
            "visited_tools": state.visited_tools,
            "tool_call_counts": state.tool_call_counts,
            "llm_calls": state.llm_calls,
            "started_at": state.started_at.isoformat(),
            "checkpoint_version": state.checkpoint_version,
            "stop_reason": state.stop_reason.value if state.stop_reason else None,
            "remaining_budget": state.remaining_budget.__dict__,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def deserialize_investigation_state(value: str) -> InvestigationState:
    try:
        payload = json.loads(value)
        from devops_agent_platform.domain.models.investigation import (
            InvestigationBudget,
        )

        return InvestigationState(
            incident_id=payload["incident_id"],
            tenant_id=payload["tenant_id"],
            completed_steps=list(payload.get("completed_steps", [])),
            failed_steps=list(payload.get("failed_steps", [])),
            evidence_ids=list(payload.get("evidence_ids", [])),
            observed_signals=list(payload.get("observed_signals", [])),
            candidate_root_services=list(payload.get("candidate_root_services", [])),
            visited_tools=list(payload.get("visited_tools", [])),
            tool_call_counts=dict(payload.get("tool_call_counts", {})),
            llm_calls=int(payload.get("llm_calls", 0)),
            started_at=datetime.fromisoformat(payload["started_at"]),
            checkpoint_version=int(payload.get("checkpoint_version", 1)),
            stop_reason=InvestigationStopReason(payload["stop_reason"])
            if payload.get("stop_reason")
            else None,
            remaining_budget=InvestigationBudget(**payload.get("remaining_budget", {})),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AppValidationError("investigation checkpoint is invalid") from exc
