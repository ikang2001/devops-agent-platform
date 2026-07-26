import pytest

from devops_agent_platform.agent.controlled_workflow import (
    build_default_observability_plan,
)
from devops_agent_platform.agent.investigation_policy import (
    InvestigationPolicy,
    build_plan_for_policy,
    parse_investigation_policy,
)
from devops_agent_platform.domain.exceptions import AppValidationError


def test_fixed_default_preserves_existing_plan_identity_and_steps() -> None:
    plan = build_plan_for_policy(InvestigationPolicy.FIXED_DEFAULT)
    existing = build_default_observability_plan()

    assert plan.plan_id == existing.plan_id
    assert plan.version == existing.version
    assert [step.tool_name for step in plan.steps] == [
        "metrics.query",
        "logs.query",
        "traces.query",
        "runbooks.retrieve",
    ]


@pytest.mark.parametrize(
    ("policy", "plan_id"),
    [
        ("fixed_no_traces", "observability-rca.no-traces"),
        (
            "fixed_metrics_logs_runbooks",
            "observability-rca.metrics-logs-runbooks",
        ),
    ],
)
def test_trace_free_policies_build_auditable_static_plans(
    policy: str,
    plan_id: str,
) -> None:
    plan = build_plan_for_policy(policy)

    assert plan.plan_id == plan_id
    assert [step.tool_name for step in plan.steps] == [
        "metrics.query",
        "logs.query",
        "runbooks.retrieve",
    ]
    assert all(step.tool_name != "traces.query" for step in plan.steps)


@pytest.mark.parametrize("value", ["auto", "", "unknown"])
def test_unknown_or_dynamic_policy_is_rejected(value: str) -> None:
    with pytest.raises(AppValidationError, match="investigation policy"):
        parse_investigation_policy(value)

    with pytest.raises(AppValidationError, match="investigation policy"):
        build_plan_for_policy(value)


def test_policy_api_normalizes_surrounding_whitespace() -> None:
    assert parse_investigation_policy(" fixed_default ") is (
        InvestigationPolicy.FIXED_DEFAULT
    )
    assert build_plan_for_policy(" fixed_default ").plan_id == (
        "default.observability-rca"
    )
