import asyncio
import json
from datetime import UTC, datetime

import pytest

from devops_agent_platform.agent.controlled_workflow import (
    ControlledAgentWorkflow,
    ControlledAgentWorkflowConfig,
    RCAWorkflowPlan,
    RCAWorkflowStep,
    build_default_observability_plan,
)
from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.application.exceptions import (
    AgentWorkflowError,
    ToolExecutionTimeoutError,
)
from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
    ToolInvocationStatus,
    ToolRiskLevel,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    PermissionDenied,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.ports.workflow import (
    AgentWorkflowExecutionFailure,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.registry import ToolRegistry
from devops_agent_platform.tools.sanitization import (
    EvidenceSanitizerConfig,
)


def build_command() -> ExecuteRCAWorkflowCommand:
    """构造已经获得执行租约的Agent命令。"""
    return ExecuteRCAWorkflowCommand(
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        incident_id="inc_001",
        operator_id="operator_001",
        worker_id="worker_001",
        execution_attempt=2,
        trace_id="trc_001",
    )


def definition(
    tool_name: str,
    *,
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
    timeout_ms: int = 1000,
) -> ToolDefinition:
    """构造受控工作流测试使用的工具定义。"""
    return ToolDefinition(
        tool_name=tool_name,
        version="v1",
        risk_level=risk_level,
        timeout_ms=timeout_ms,
        permission_tags=(f"{tool_name}:read",),
    )


def build_plan() -> RCAWorkflowPlan:
    """构造固定的指标、日志两步RCA计划。"""
    return RCAWorkflowPlan(
        plan_id="default.rca",
        version="v1",
        steps=(
            RCAWorkflowStep(
                step_id="collect.metrics",
                tool_name="metrics.query",
                tool_version="v1",
                payload={"window_minutes": 15},
            ),
            RCAWorkflowStep(
                step_id="collect.logs",
                tool_name="logs.query",
                tool_version="v1",
                payload={"limit": 200},
            ),
        ),
    )


class RecordingPermissionChecker:
    """记录授权顺序，并可拒绝指定工具。"""

    def __init__(
        self,
        events: list[str],
        denied_tool: str | None = None,
    ) -> None:
        self._events = events
        self._denied_tool = denied_tool

    async def check(
        self,
        definition: ToolDefinition,
        tenant_id: str,
        operator_id: str | None,
    ) -> None:
        self._events.append(f"permission:{definition.tool_name}")
        assert tenant_id == "tenant_001"
        assert operator_id == "operator_001"
        if definition.tool_name == self._denied_tool:
            raise PermissionDenied("tool permission denied")


class RecordingToolExecutor:
    """记录工具调用，并支持失败、阻塞和自定义结果。"""

    def __init__(
        self,
        events: list[str],
        *,
        failed_tool: str | None = None,
        blocking_tool: str | None = None,
        results: dict[str, object] | None = None,
    ) -> None:
        self._events = events
        self._failed_tool = failed_tool
        self._blocking_tool = blocking_tool
        self._results = results or {}
        self.payloads: dict[str, dict] = {}
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def execute(
        self,
        definition: ToolDefinition,
        payload: dict,
        trace_id: str,
    ) -> dict:
        self._events.append(f"execute:{definition.tool_name}")
        self.payloads[definition.tool_name] = payload
        assert trace_id == "trc_001"
        if definition.tool_name == self._blocking_tool:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if definition.tool_name == self._failed_tool:
            raise RuntimeError("tool failed")
        return self._results.get(
            definition.tool_name,
            {"status": "ok"},
        )  # type: ignore[return-value]


class StaticReportGenerator:
    """返回高置信度固定报告，用于验证工作流侧护栏。"""

    def __init__(
        self,
        *,
        confidence: float = 0.95,
        conclusion_status: RCAConclusionStatus = RCAConclusionStatus.CANDIDATE,
    ) -> None:
        self._confidence = confidence
        self._conclusion_status = conclusion_status

    async def generate(self, command, evidence) -> RCAReport:
        type_names = sorted({item.evidence_type.value for item in evidence})
        type_counts = tuple(
            (
                type_name,
                sum(
                    item.evidence_type.value == type_name
                    for item in evidence
                ),
            )
            for type_name in type_names
        )
        return RCAReport(
            report_id="static-report",
            tenant_id=command.tenant_id,
            incident_id=command.incident_id,
            workflow_run_id=command.workflow_run_id,
            execution_attempt=command.execution_attempt,
            conclusion_status=self._conclusion_status,
            title="Generated candidate",
            summary="Generated from collected evidence.",
            confidence=self._confidence,
            evidence_ids=tuple(item.evidence_id for item in evidence),
            evidence_type_counts=type_counts,
            recommendations=("Review the cited evidence.",),
            generator_name="static-test-generator",
            generator_version="v1",
            generated_at=datetime(2026, 7, 26, 0, 0, tzinfo=UTC),
        )


def build_workflow(
    *,
    plan: RCAWorkflowPlan | None = None,
    registry: ToolRegistry | None = None,
    permission_checker: RecordingPermissionChecker | None = None,
    executor: RecordingToolExecutor | None = None,
    config: ControlledAgentWorkflowConfig | None = None,
    clock=None,
    monotonic_clock=None,
    report_generator=None,
    events: list[str] | None = None,
) -> tuple[
    ControlledAgentWorkflow,
    RecordingPermissionChecker,
    RecordingToolExecutor,
]:
    """组装完整受控工作流及可观测替身。"""
    shared_events = events if events is not None else []
    resolved_permission = permission_checker or RecordingPermissionChecker(
        shared_events
    )
    resolved_executor = executor or RecordingToolExecutor(shared_events)
    resolved_registry = registry or ToolRegistry(
        (
            definition("metrics.query"),
            definition("logs.query", risk_level=ToolRiskLevel.MEDIUM),
        )
    )
    return (
        ControlledAgentWorkflow(
            plan=plan or build_plan(),
            registry=resolved_registry,
            permission_checker=resolved_permission,
            tool_executor=resolved_executor,
            config=config,
            clock=clock,
            monotonic_clock=monotonic_clock,
            report_generator=report_generator,
        ),
        resolved_permission,
        resolved_executor,
    )


async def test_all_steps_are_preflighted_then_executed_in_fixed_order() -> None:
    """所有授权预检完成后，工具才按服务端固定顺序执行。"""
    events: list[str] = []
    workflow, _, executor = build_workflow(
        events=events,
        clock=lambda: datetime(2026, 6, 29, 10, 0, tzinfo=UTC),
    )

    result = await workflow.execute(build_command())

    assert events == [
        "permission:metrics.query",
        "permission:logs.query",
        "execute:metrics.query",
        "execute:logs.query",
    ]
    metrics_payload = executor.payloads["metrics.query"]
    assert metrics_payload["window_minutes"] == 15
    assert metrics_payload["tenant_id"] == "tenant_001"
    assert metrics_payload["workflow_run_id"] == "wfr_001"
    assert metrics_payload["execution_attempt"] == 2
    assert metrics_payload["plan_id"] == "default.rca"
    assert metrics_payload["step_id"] == "collect.metrics"
    assert [item.evidence_type for item in result.evidence] == [
        EvidenceType.METRIC,
        EvidenceType.LOG,
    ]
    assert result.evidence[0].source == "prometheus"
    assert result.evidence[0].workflow_run_id == "wfr_001"
    assert result.evidence[0].execution_attempt == 2
    assert len(result.invocations) == 2
    assert all(
        item.status is ToolInvocationStatus.SUCCEEDED for item in result.invocations
    )
    assert result.invocations[0].input_summary.startswith("payload_fields=")
    assert result.invocations[0].output_sha256 == (result.evidence[0].content_sha256)
    assert result.report is not None
    assert result.report.evidence_ids == tuple(
        sorted(item.evidence_id for item in result.evidence)
    )
    assert result.report.confidence == 0.0


async def test_evidence_is_sanitized_without_mutating_tool_result() -> None:
    """持久化副本必须脱敏，同时保留工具返回对象供当前执行继续使用。"""
    events: list[str] = []
    raw_result = {
        "source": "prometheus token=source-secret",
        "summary": "authorization=Bearer private-token",
        "nested": {
            "db_password": "hunter2",
            "url": "postgresql://alice:secret@db.local/app",
        },
    }
    executor = RecordingToolExecutor(
        events,
        results={"metrics.query": raw_result},
    )
    workflow, _, _ = build_workflow(executor=executor, events=events)

    result = await workflow.execute(build_command())
    persisted = json.loads(result.evidence[0].content_json)

    assert "private-token" not in str(persisted)
    assert "source-secret" not in str(persisted)
    assert "hunter2" not in str(persisted)
    assert "alice:secret" not in str(persisted)
    assert result.evidence[0].source == "prometheus token=[REDACTED]"
    assert result.evidence[0].summary == "authorization=[REDACTED]"
    assert result.invocations[0].output_summary == "authorization=[REDACTED]"
    assert persisted["nested"]["db_password"] == "[REDACTED]"
    assert result.invocations[0].output_sha256 == (result.evidence[0].content_sha256)
    assert "source-secret" not in result.evidence[0].source
    assert "private-token" not in result.evidence[0].summary
    assert raw_result["nested"]["db_password"] == "hunter2"


async def test_public_evidence_fields_fall_back_when_control_chars_appear() -> None:
    """工具返回的公开字段带控制字符时应回退，避免污染 Evidence 视图。"""
    events: list[str] = []
    executor = RecordingToolExecutor(
        events,
        results={
            "metrics.query": {
                "source": "prometheus\x7fforged",
                "summary": "healthy summary\tforged",
                "signals": [1, 2, 3],
            }
        },
    )
    workflow, _, _ = build_workflow(executor=executor, events=events)

    result = await workflow.execute(build_command())
    evidence = result.evidence[0]

    assert evidence.source == "prometheus"
    assert evidence.summary == (
        "metrics.query collected evidence from prometheus; signals=3; truncated=False"
    )
    assert "\x7f" not in evidence.source
    assert "\t" not in evidence.summary
    assert result.invocations[0].output_summary == evidence.summary


async def test_unsafe_evidence_structure_fails_current_tool() -> None:
    """违反结构策略的结果应形成失败审计并停止后续步骤。"""
    events: list[str] = []
    executor = RecordingToolExecutor(
        events,
        results={"metrics.query": {"level1": {"level2": {"value": "too deep"}}}},
    )
    workflow, _, _ = build_workflow(
        executor=executor,
        config=ControlledAgentWorkflowConfig(
            evidence_sanitizer=EvidenceSanitizerConfig(max_depth=1)
        ),
        events=events,
    )

    with pytest.raises(AgentWorkflowExecutionFailure) as captured:
        await workflow.execute(build_command())

    assert isinstance(captured.value.cause, AgentWorkflowError)
    assert captured.value.result.invocations[0].status is (ToolInvocationStatus.FAILED)
    assert "execute:logs.query" not in events


async def test_report_failure_preserves_completed_audit_as_partial_result() -> None:
    """报告失败不能伪装成功，已完成调用仍要交给失败事务审计。"""

    class FailingReportGenerator:
        async def generate(self, command, evidence):
            del command, evidence
            raise RuntimeError("report generation failed")

    events: list[str] = []
    workflow, _, _ = build_workflow(
        events=events,
        report_generator=FailingReportGenerator(),
    )

    with pytest.raises(AgentWorkflowExecutionFailure) as captured:
        await workflow.execute(build_command())

    assert isinstance(captured.value.cause, RuntimeError)
    assert len(captured.value.result.evidence) == 2
    assert len(captured.value.result.invocations) == 2
    assert captured.value.result.report is None


async def test_missing_tool_version_prevents_all_tool_execution() -> None:
    """后续步骤缺少明确版本时，前面步骤也不能提前执行。"""
    events: list[str] = []
    workflow, _, executor = build_workflow(
        registry=ToolRegistry((definition("metrics.query"),)),
        events=events,
    )

    with pytest.raises(ResourceNotFound, match="logs.query"):
        await workflow.execute(build_command())

    assert executor.payloads == {}
    assert not any(event.startswith("execute:") for event in events)


async def test_permission_denial_prevents_all_tool_execution() -> None:
    """任一步权限不足时整份计划都不得开始执行。"""
    events: list[str] = []
    permission = RecordingPermissionChecker(
        events,
        denied_tool="logs.query",
    )
    workflow, _, executor = build_workflow(
        permission_checker=permission,
        events=events,
    )

    with pytest.raises(PermissionDenied):
        await workflow.execute(build_command())

    assert executor.payloads == {}


async def test_unattended_workflow_rejects_disallowed_risk() -> None:
    """未接人工审批端口时，风险超界工具不能进入执行阶段。"""
    events: list[str] = []
    registry = ToolRegistry(
        (
            definition("metrics.query"),
            definition("logs.query", risk_level=ToolRiskLevel.HIGH),
        )
    )
    workflow, _, executor = build_workflow(
        registry=registry,
        events=events,
    )

    with pytest.raises(PermissionDenied, match="risk level"):
        await workflow.execute(build_command())

    assert executor.payloads == {}


async def test_tool_timeout_cancels_underlying_execution() -> None:
    """单步超过ToolDefinition超时后取消协程并停止计划。"""
    events: list[str] = []
    executor = RecordingToolExecutor(
        events,
        blocking_tool="metrics.query",
    )
    registry = ToolRegistry(
        (
            definition("metrics.query", timeout_ms=10),
            definition("logs.query"),
        )
    )
    workflow, _, _ = build_workflow(
        registry=registry,
        executor=executor,
        events=events,
    )

    with pytest.raises(AgentWorkflowExecutionFailure) as captured:
        await workflow.execute(build_command())

    assert isinstance(captured.value.cause, ToolExecutionTimeoutError)
    assert "metrics.query" in str(captured.value.cause)
    assert captured.value.result.invocations[0].status is (ToolInvocationStatus.FAILED)
    assert captured.value.result.invocations[0].error_code == (
        "ToolExecutionTimeoutError"
    )
    assert executor.cancelled is True
    assert "execute:logs.query" not in events


async def test_tool_failure_stops_following_steps() -> None:
    """工具业务异常由协调器收口为失败，后续步骤不再执行。"""
    events: list[str] = []
    executor = RecordingToolExecutor(
        events,
        failed_tool="metrics.query",
    )
    workflow, _, _ = build_workflow(executor=executor, events=events)

    with pytest.raises(AgentWorkflowExecutionFailure) as captured:
        await workflow.execute(build_command())

    assert isinstance(captured.value.cause, RuntimeError)
    assert str(captured.value.cause) == "tool failed"
    assert captured.value.result.invocations[0].error_code == "RuntimeError"
    assert events[-1] == "execute:metrics.query"
    assert "execute:logs.query" not in events


async def test_continue_on_failure_returns_partial_guarded_report() -> None:
    """部分采集只能生成带失败标记、低置信度且非 CONFIRMED 的报告。"""
    events: list[str] = []
    executor = RecordingToolExecutor(
        events,
        failed_tool="metrics.query",
    )
    workflow, _, _ = build_workflow(
        executor=executor,
        config=ControlledAgentWorkflowConfig(continue_on_step_failure=True),
        report_generator=StaticReportGenerator(
            conclusion_status=RCAConclusionStatus.CONFIRMED,
        ),
        events=events,
    )

    result = await workflow.execute(build_command())

    assert [item.status for item in result.invocations] == [
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.SUCCEEDED,
    ]
    assert [item.evidence_type for item in result.evidence] == [EvidenceType.LOG]
    assert result.report is not None
    assert result.report.conclusion_status is RCAConclusionStatus.UNDETERMINED
    assert result.report.confidence == 0.4
    assert "Partial collection: failed steps=collect.metrics" in (
        result.report.summary
    )
    assert events[-1] == "execute:logs.query"


async def test_continue_on_failure_rejects_zero_evidence() -> None:
    """即使开启降级，所有步骤均失败时也不能伪造空 RCA 报告。"""
    events: list[str] = []
    executor = RecordingToolExecutor(
        events,
        results={
            "metrics.query": ["invalid"],
            "logs.query": ["invalid"],
        },
    )
    workflow, _, _ = build_workflow(
        executor=executor,
        config=ControlledAgentWorkflowConfig(continue_on_step_failure=True),
        events=events,
    )

    with pytest.raises(AgentWorkflowExecutionFailure) as captured:
        await workflow.execute(build_command())

    assert captured.value.result.evidence == ()
    assert [item.status for item in captured.value.result.invocations] == [
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.FAILED,
    ]
    assert events[-2:] == ["execute:metrics.query", "execute:logs.query"]


async def test_report_confidence_respects_configured_cap() -> None:
    """完整采集也只能下调生成器分数，不能超过工作流配置上限。"""
    workflow, _, _ = build_workflow(
        config=ControlledAgentWorkflowConfig(report_confidence_cap=0.6),
        report_generator=StaticReportGenerator(confidence=0.95),
    )

    result = await workflow.execute(build_command())

    assert result.report is not None
    assert result.report.conclusion_status is RCAConclusionStatus.CANDIDATE
    assert result.report.confidence == 0.6


async def test_outer_cancellation_propagates_to_tool() -> None:
    """租约失权或Runtime停机取消时，正在执行的工具必须收到取消。"""
    events: list[str] = []
    executor = RecordingToolExecutor(
        events,
        blocking_tool="metrics.query",
    )
    workflow, _, _ = build_workflow(executor=executor, events=events)
    task = asyncio.create_task(workflow.execute(build_command()))
    await executor.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert executor.cancelled is True
    assert "execute:logs.query" not in events


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (["not", "an", "object"], "must be an object"),
        ({"bad": object()}, "JSON serializable"),
        ({"content": "x" * 1024}, "exceeds"),
    ],
)
async def test_invalid_or_oversized_tool_result_stops_workflow(
    result: object,
    message: str,
) -> None:
    """不可信工具输出必须经过类型、JSON和体积校验。"""
    events: list[str] = []
    executor = RecordingToolExecutor(
        events,
        results={"metrics.query": result},
    )
    workflow, _, _ = build_workflow(
        executor=executor,
        config=ControlledAgentWorkflowConfig(max_result_bytes=128),
        events=events,
    )

    with pytest.raises(AgentWorkflowExecutionFailure) as captured:
        await workflow.execute(build_command())

    assert isinstance(captured.value.cause, AgentWorkflowError)
    assert message in str(captured.value.cause)
    assert captured.value.result.invocations[0].status is (ToolInvocationStatus.FAILED)
    assert "execute:logs.query" not in events


def test_plan_and_configuration_reject_unsafe_structure() -> None:
    """保留字段、重复步骤、高风险配置和超长计划在启动时失败。"""
    with pytest.raises(AppValidationError, match="reserved fields"):
        RCAWorkflowStep(
            step_id="bad.step",
            tool_name="logs.query",
            tool_version="v1",
            payload={"tenant_id": "attacker"},
        )

    duplicate = RCAWorkflowStep(
        step_id="same.step",
        tool_name="logs.query",
        tool_version="v1",
        payload={},
    )
    with pytest.raises(AppValidationError, match="unique"):
        RCAWorkflowPlan(
            plan_id="bad.plan",
            version="v1",
            steps=(duplicate, duplicate),
        )

    with pytest.raises(AppValidationError, match="approval"):
        ControlledAgentWorkflowConfig(
            allowed_risk_levels=frozenset({ToolRiskLevel.HIGH})
        )

    with pytest.raises(AppValidationError, match="partial_report_confidence_cap"):
        ControlledAgentWorkflowConfig(
            report_confidence_cap=0.3,
            partial_report_confidence_cap=0.4,
        )

    with pytest.raises(AppValidationError, match="between 0 and 1"):
        ControlledAgentWorkflowConfig(report_confidence_cap=float("nan"))

    with pytest.raises(AppValidationError, match="must not exceed"):
        build_workflow(
            config=ControlledAgentWorkflowConfig(max_steps=1),
        )


def test_default_observability_plan_is_fixed_and_read_only_oriented() -> None:
    """默认计划固定可观测信号、Runbook顺序及有界查询参数。"""
    plan = build_default_observability_plan()

    assert [step.tool_name for step in plan.steps] == [
        "metrics.query",
        "logs.query",
        "traces.query",
        "runbooks.retrieve",
    ]
    assert plan.version == "v2"
    assert plan.steps[0].payload["max_series"] == 200
    assert plan.steps[1].payload["limit"] == 500
    assert plan.steps[2].payload["limit"] == 100
    assert plan.steps[3].payload["max_results"] == 5
