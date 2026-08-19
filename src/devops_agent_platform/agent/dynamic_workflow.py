from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from devops_agent_platform.agent.bounded_dynamic import (
    BoundedDynamicInvestigator,
    InvestigationPolicyValidator,
)
from devops_agent_platform.agent.controlled_workflow import (
    ControlledAgentWorkflow,
    ControlledAgentWorkflowConfig,
    RCAWorkflowPlan,
    RCAWorkflowStep,
)
from devops_agent_platform.agent.report_generator import (
    DeterministicRCAReportGenerator,
)
from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.causal_context import TopologyCausalContextBuilder
from devops_agent_platform.domain.enums import (
    RCAConclusionStatus,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.investigation import (
    InvestigationBudget,
    InvestigationState,
    InvestigationStopReason,
    StepDecision,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.domain.models.topology import TopologyGraph
from devops_agent_platform.ports.investigation import InvestigationCheckpointPort
from devops_agent_platform.ports.rca_report import RCAReportGeneratorPort
from devops_agent_platform.ports.workflow import (
    AgentWorkflowExecutionFailure,
    AgentWorkflowResult,
)
from devops_agent_platform.tools.executor import ToolExecutor
from devops_agent_platform.tools.permission import ToolPermissionChecker
from devops_agent_platform.tools.registry import ToolRegistry

_RESERVED_FIELDS = frozenset(
    {
        "execution_attempt",
        "incident_id",
        "operator_id",
        "plan_id",
        "plan_version",
        "step_id",
        "tenant_id",
        "trace_id",
        "worker_id",
        "workflow_run_id",
    }
)


@dataclass(frozen=True)
class DynamicIncidentContext:
    service_name: str
    environment: str = "default"
    summary: str = ""
    error_fingerprint: str = ""


class RuntimeIntentPlanner:
    """服务端安全的有限规划器；模型可以替换它，但不能越过后端边界。"""

    _TOOLS = (
        "topology.query@v1",
        "metrics.query@v1",
        "changes.query@v1",
        "logs.query@v1",
        "traces.query@v1",
        "knowledge.search@v1",
        "runbooks.retrieve@v1",
    )

    async def next_step(self, state: InvestigationState) -> StepDecision:
        visited = set(state.visited_tools)
        # 拓扑和知识是动态 RCA 的强制上下文；其余步骤依据已收集信号渐进选择。
        if self._TOOLS[0] not in visited:
            return StepDecision(
                next_tool=self._TOOLS[0],
                reason_code="ESTABLISH_TOPOLOGY_CONTEXT",
            )
        if self._TOOLS[1] not in visited:
            return StepDecision(
                next_tool=self._TOOLS[1],
                target_service=state.service_name,
                reason_code="MEASURE_PRIMARY_SERVICE",
            )
        if self._TOOLS[3] not in visited:
            return StepDecision(
                next_tool=self._TOOLS[3],
                # Logs are tenant/time-window scoped and must remain queryable
                # even when topology has no node for a newly observed service.
                target_service=None,
                reason_code=(
                    "FOLLOW_ERROR_SIGNALS"
                    if state.observed_signals
                    else "LOOK_FOR_ERROR_LOGS"
                ),
            )
        if self._TOOLS[2] not in visited and (
            state.observed_signals or self._TOOLS[1] in state.completed_steps
        ):
            return StepDecision(
                next_tool=self._TOOLS[2],
                reason_code="CORRELATE_RECENT_CHANGES",
            )
        if self._TOOLS[4] not in visited and (
            "latency_p95" in state.observed_signals
            or "timeouts" in state.observed_signals
            or self._TOOLS[3] in state.completed_steps
        ):
            return StepDecision(
                next_tool=self._TOOLS[4],
                reason_code="TRACE_SLOW_OR_FAILED_REQUESTS",
            )
        if self._TOOLS[5] not in visited:
            return StepDecision(
                next_tool=self._TOOLS[5],
                target_service=state.service_name,
                reason_code="CHECK_HISTORICAL_KNOWLEDGE",
            )
        if self._TOOLS[6] not in visited:
            return StepDecision(
                next_tool=self._TOOLS[6],
                reason_code="RETRIEVE_PUBLISHED_RUNBOOKS",
            )
        return StepDecision(
            next_tool=None,
            reason_code="EVIDENCE_SUFFICIENT",
            stop=True,
        )


@dataclass(frozen=True)
class _DynamicExecutionContext:
    command: ExecuteRCAWorkflowCommand
    topology: TopologyGraph | None
    incident: DynamicIncidentContext


class BoundedDynamicRCAWorkflow:
    """把有界调查器适配为现有 AgentWorkflowPort 的运行时实现。"""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        permission_checker: ToolPermissionChecker,
        tool_executor: ToolExecutor,
        report_generator: RCAReportGeneratorPort | None = None,
        checkpoint: InvestigationCheckpointPort | None = None,
        context_loader: Callable[
            [str, str], Awaitable[tuple[DynamicIncidentContext, TopologyGraph | None]]
        ] | None = None,
        budget: InvestigationBudget | None = None,
    ) -> None:
        self._registry = registry
        self._permission_checker = permission_checker
        self._tool_executor = tool_executor
        self._report_generator = report_generator or DeterministicRCAReportGenerator()
        self._checkpoint = checkpoint
        self._context_loader = context_loader
        self._budget = budget or InvestigationBudget()

    async def execute(self, command: ExecuteRCAWorkflowCommand) -> AgentWorkflowResult:
        context = await self._load_context(command)
        state = InvestigationState(
            incident_id=command.incident_id,
            tenant_id=command.tenant_id,
            service_name=context.incident.service_name,
            environment=context.incident.environment,
            alert_summary=context.incident.summary,
            error_fingerprint=context.incident.error_fingerprint,
            remaining_budget=self._budget,
        )
        validator = InvestigationPolicyValidator(self._registry)
        investigator = BoundedDynamicInvestigator(
            validator,
            checkpoint=self._checkpoint,
        )
        evidence: list[Evidence] = []
        invocations: list[ToolInvocation] = []

        async def execute_tool(
            tool_key: str,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            tool_name, version = tool_key.rsplit("@", 1)
            step_id = self._step_id(state.step_count + 1, tool_name)
            safe_step_payload = {
                key: value
                for key, value in payload.items()
                if key not in _RESERVED_FIELDS
            }
            plan = RCAWorkflowPlan(
                plan_id="bounded-dynamic-rca",
                version="v1",
                steps=(
                    RCAWorkflowStep(
                        step_id=step_id,
                        tool_name=tool_name,
                        tool_version=version,
                        payload=safe_step_payload,
                    ),
                ),
            )
            one_step_workflow = ControlledAgentWorkflow(
                plan=plan,
                registry=self._registry,
                permission_checker=self._permission_checker,
                tool_executor=self._tool_executor,
                config=ControlledAgentWorkflowConfig(
                    continue_on_step_failure=False,
                ),
                report_generator=DeterministicRCAReportGenerator(),
            )
            try:
                result = await one_step_workflow.execute(command)
            except AgentWorkflowExecutionFailure as exc:
                evidence.extend(exc.result.evidence)
                invocations.extend(exc.result.invocations)
                raise
            evidence.extend(result.evidence)
            invocations.extend(result.invocations)
            self._observe_result(state, result.evidence)
            return {"evidence_ids": [item.evidence_id for item in result.evidence]}

        await investigator.run(
            state,
            RuntimeIntentPlanner(),
            execute_tool=execute_tool,
            topology=context.topology,
        )
        aggregate = AgentWorkflowResult(
            evidence=tuple(evidence),
            invocations=tuple(invocations),
        )
        if not evidence:
            failure = AppValidationError(
                f"bounded dynamic investigation stopped: {state.stop_reason}"
            )
            raise AgentWorkflowExecutionFailure(aggregate, failure) from failure
        try:
            report = await self._report_generator.generate(command, tuple(evidence))
            report = self._apply_guardrails(
                report,
                state,
                context.topology,
                tuple(evidence),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AgentWorkflowExecutionFailure(aggregate, exc) from exc
        return AgentWorkflowResult(
            evidence=tuple(evidence),
            invocations=tuple(invocations),
            report=report,
        )

    async def _load_context(
        self, command: ExecuteRCAWorkflowCommand
    ) -> _DynamicExecutionContext:
        if self._context_loader is None:
            incident = DynamicIncidentContext(service_name="unknown")
            return _DynamicExecutionContext(command, None, incident)
        incident, topology = await self._context_loader(
            command.tenant_id,
            command.incident_id,
        )
        return _DynamicExecutionContext(command, topology, incident)

    @staticmethod
    def _step_id(index: int, tool_name: str) -> str:
        suffix = re.sub(r"[^a-z0-9]+", "-", tool_name).strip("-")
        return f"dynamic-{index}-{suffix}"[:128]

    @staticmethod
    def _observe_result(
        state: InvestigationState,
        evidence: tuple[Evidence, ...],
    ) -> None:
        for item in evidence:
            try:
                content = item.content_json
            except AttributeError:
                continue
            for signal in (
                "availability",
                "request_rate",
                "error_ratio",
                "latency_p95",
                "errors",
                "timeouts",
                "resource_pressure",
                "slow_spans",
            ):
                if signal in content and signal not in state.observed_signals:
                    state.observed_signals.append(signal)
            if item.tool_name == "changes.query":
                state.recent_change_type = "recent_change"

    @staticmethod
    def _apply_guardrails(
        report: RCAReport,
        state: InvestigationState,
        topology: TopologyGraph | None,
        evidence: tuple[Evidence, ...],
    ) -> RCAReport:
        if report.conclusion_status is RCAConclusionStatus.CONFIRMED:
            report = replace(
                report,
                conclusion_status=RCAConclusionStatus.UNDETERMINED,
            )
        if state.stop_reason is not InvestigationStopReason.EVIDENCE_SUFFICIENT:
            report = replace(report, confidence=min(float(report.confidence), 0.4))
        if state.failed_steps:
            marker = " Partial collection: failed steps=" + ",".join(
                state.failed_steps
            ) + "."
            report = replace(
                report,
                summary=report.summary[: 4096 - len(marker)] + marker,
            )
        causal_context = TopologyCausalContextBuilder().build(
            report=report,
            incident_service=state.service_name,
            topology=topology,
            evidence=evidence,
        )
        return replace(
            report,
            suspected_root_node=causal_context.root_node,
            affected_services=causal_context.affected_services,
            causal_chain=causal_context.causal_chain,
            blast_radius=causal_context.blast_radius,
        )


DynamicRCAWorkflowAdapter = BoundedDynamicRCAWorkflow
