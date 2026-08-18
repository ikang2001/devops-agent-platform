"""RCA 调查策略：仅允许服务端发布的固定计划变体，不做自由自适应循环。"""

from enum import StrEnum

from devops_agent_platform.agent.controlled_workflow import (
    RCAWorkflowPlan,
    RCAWorkflowStep,
    build_default_observability_plan,
)
from devops_agent_platform.domain.exceptions import AppValidationError


class InvestigationPolicy(StrEnum):
    """可配置调查策略键；全部是静态计划，不是运行时推理。"""

    # 默认：metrics → logs → traces → runbooks
    FIXED_DEFAULT = "fixed_default"
    # 省略 traces（部分环境无 Tempo 时的只读降级计划）
    FIXED_NO_TRACES = "fixed_no_traces"
    # 仅 metrics + logs + runbooks
    FIXED_METRICS_LOGS_RUNBOOKS = "fixed_metrics_logs_runbooks"
    BOUNDED_DYNAMIC_V1 = "bounded_dynamic_v1"


def build_plan_for_policy(policy: InvestigationPolicy | str) -> RCAWorkflowPlan:
    """按策略键构造不可变固定计划；未知键直接失败，禁止静默回落。"""
    if isinstance(policy, str):
        policy = parse_investigation_policy(policy)
    if not isinstance(policy, InvestigationPolicy):
        raise AppValidationError("investigation policy is invalid")

    if policy is InvestigationPolicy.FIXED_DEFAULT:
        return build_default_observability_plan()

    if policy is InvestigationPolicy.FIXED_NO_TRACES:
        return _build_metrics_logs_runbooks_plan(
            "observability-rca.no-traces",
            include_changes=True,
        )

    if policy is InvestigationPolicy.FIXED_METRICS_LOGS_RUNBOOKS:
        return _build_metrics_logs_runbooks_plan(
            "observability-rca.metrics-logs-runbooks",
            include_changes=False,
        )

    if policy is InvestigationPolicy.BOUNDED_DYNAMIC_V1:
        # 动态调查由 BoundedDynamicInvestigator 驱动；该计划作为审计与回放的
        # 安全基线，仍然只允许现有只读观测工具。
        return build_default_observability_plan()

    raise AppValidationError(f"unsupported investigation policy: {policy}")


def parse_investigation_policy(value: str) -> InvestigationPolicy:
    """解析配置字符串为策略枚举。"""
    if not isinstance(value, str) or not value.strip():
        raise AppValidationError("investigation policy is required")
    try:
        return InvestigationPolicy(value.strip())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in InvestigationPolicy)
        raise AppValidationError(
            f"investigation policy must be one of: {allowed}"
        ) from exc


def _build_metrics_logs_runbooks_plan(
    plan_id: str,
    *,
    include_changes: bool,
) -> RCAWorkflowPlan:
    """构造无 Trace 固定计划；不同 plan_id 保留部署意图审计。"""
    change_steps = (
        (
            RCAWorkflowStep(
                step_id="collect.changes",
                tool_name="changes.query",
                tool_version="v1",
                payload={"max_results": 20},
            ),
        )
        if include_changes
        else ()
    )
    return RCAWorkflowPlan(
        plan_id=plan_id,
        version="v2" if include_changes else "v1",
        steps=(
            RCAWorkflowStep(
                step_id="collect.metrics",
                tool_name="metrics.query",
                tool_version="v1",
                payload={"window_minutes": 15, "max_series": 200},
            ),
            *change_steps,
            RCAWorkflowStep(
                step_id="collect.logs",
                tool_name="logs.query",
                tool_version="v1",
                payload={"window_minutes": 15, "limit": 500},
            ),
            RCAWorkflowStep(
                step_id="retrieve.runbooks",
                tool_name="runbooks.retrieve",
                tool_version="v1",
                payload={"max_results": 5},
            ),
        ),
    )
