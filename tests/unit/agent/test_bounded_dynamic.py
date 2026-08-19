import pytest

from devops_agent_platform.agent.bounded_dynamic import (
    BoundedDynamicInvestigator,
    InvestigationPolicyValidator,
    deserialize_investigation_state,
    serialize_investigation_state,
)
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.models.investigation import (
    InvestigationBudget,
    InvestigationState,
    StepDecision,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.registry import ToolRegistry


class Handler:
    async def execute(self, payload, trace_id):
        return {"evidence_ids": ["ev-1"]}


class Planner:
    async def next_step(self, state):
        if state.step_count:
            return StepDecision(next_tool=None, reason_code="DONE", stop=True)
        return StepDecision(
            next_tool="metrics.query@v1",
            reason_code="START",
            intent={"payload": {"sql": "drop table"}},
        )


class TimeoutPlanner:
    async def next_step(self, state):
        del state
        raise TimeoutError("planner timed out")


class InvalidPlanner:
    async def next_step(self, state):
        del state
        return {"next_tool": "metrics.query@v1"}


@pytest.mark.asyncio
async def test_dynamic_policy_builds_safe_payload_and_checkpoint_round_trips():
    registry = ToolRegistry()
    handlers = ToolHandlerRegistry()
    definition = ToolDefinition(
        tool_name="metrics.query",
        version="v1",
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=1000,
        permission_tags=("metrics:read",),
    )
    registry.register(definition)
    handlers.register(definition, Handler())
    state = InvestigationState(
        incident_id="incident",
        tenant_id="tenant",
        remaining_budget=InvestigationBudget(max_steps=2),
    )
    validator = InvestigationPolicyValidator(registry)
    seen = []

    async def execute(tool, payload):
        seen.append((tool, payload))
        return {"evidence_ids": ["ev-1"]}

    result = await BoundedDynamicInvestigator(validator).run(
        state, Planner(), execute_tool=execute
    )
    assert result.evidence_ids == ["ev-1"]
    assert "sql" not in seen[0][1]
    assert (
        deserialize_investigation_state(
            serialize_investigation_state(state)
        ).checkpoint_version
        == state.checkpoint_version
    )


@pytest.mark.asyncio
async def test_planner_failure_stops_with_failed_reason() -> None:
    state = InvestigationState(incident_id="incident", tenant_id="tenant")
    result = await BoundedDynamicInvestigator(
        InvestigationPolicyValidator(ToolRegistry())
    ).run(
        state,
        TimeoutPlanner(),
        execute_tool=lambda *_args: None,
    )

    assert result.stop_reason.value == "FAILED"


@pytest.mark.asyncio
async def test_invalid_planner_result_is_policy_blocked() -> None:
    state = InvestigationState(incident_id="incident", tenant_id="tenant")
    result = await BoundedDynamicInvestigator(
        InvestigationPolicyValidator(ToolRegistry())
    ).run(
        state,
        InvalidPlanner(),
        execute_tool=lambda *_args: None,
    )

    assert result.stop_reason.value == "POLICY_BLOCKED"


@pytest.mark.asyncio
async def test_successful_last_budget_step_is_evidence_sufficient() -> None:
    state = InvestigationState(
        incident_id="incident",
        tenant_id="tenant",
        remaining_budget=InvestigationBudget(max_steps=1),
    )

    async def execute_tool(*_args):
        return {"evidence_ids": ["ev-1"]}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_name="metrics.query",
            version="v1",
            risk_level=ToolRiskLevel.LOW,
            timeout_ms=1000,
            permission_tags=("metrics:read",),
        )
    )

    result = await BoundedDynamicInvestigator(
        InvestigationPolicyValidator(registry)
    ).run(
        state,
        Planner(),
        execute_tool=execute_tool,
    )

    assert result.stop_reason.value == "EVIDENCE_SUFFICIENT"
