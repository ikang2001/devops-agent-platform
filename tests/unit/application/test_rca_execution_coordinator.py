import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.commands.workflow_execution import (
    CompleteWorkflowRunCommand,
    ExecuteRCAWorkflowCommand,
    HeartbeatWorkflowRunCommand,
)
from devops_agent_platform.application.exceptions import (
    PersistenceError,
    WorkflowLeaseLostError,
)
from devops_agent_platform.application.messages.rca_requested import (
    RCARequestedEventV1,
)
from devops_agent_platform.application.services.rca_execution_coordinator import (
    RCAExecutionCoordinator,
    RCAExecutionCoordinatorConfig,
)
from devops_agent_platform.application.services.workflow_execution_service import (
    WorkflowClaimResult,
    WorkflowCompletionResult,
    WorkflowHeartbeatResult,
)
from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
    ToolInvocationStatus,
    ToolRiskLevel,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.ports.workflow import (
    AgentWorkflowExecutionFailure,
    AgentWorkflowResult,
)

NOW = datetime(2026, 6, 28, 20, 0, tzinfo=UTC)


def build_event() -> RCARequestedEventV1:
    """构造协调器使用的已校验RCA消息。"""
    return RCARequestedEventV1(
        event_id="evt_rca_001",
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        incident_id="inc_001",
        operator_id="operator_001",
        occurred_at=NOW,
        requested_at=NOW,
        trace_id="trc_001",
    )


def build_claim(**overrides: object) -> WorkflowClaimResult:
    """构造当前Worker刚获得的运行租约结果。"""
    values: dict[str, object] = {
        "workflow_run_id": "wfr_001",
        "status": WorkflowRunStatus.RUNNING.value,
        "acquired": True,
        "lease_owner": "worker_001",
        "lease_expires_at": NOW + timedelta(minutes=2),
        "execution_attempts": 1,
        "trace_id": "trc_001",
    }
    values.update(overrides)
    return WorkflowClaimResult(**values)  # type: ignore[arg-type]


def build_evidence() -> Evidence:
    """构造协调器测试使用的 Agent 成功证据。"""
    content_json, content_sha256 = build_evidence_content({"status": "ok"})
    return Evidence(
        evidence_id="e" * 64,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id="collect.logs",
        tool_name="logs.query",
        tool_version="v1",
        evidence_type=EvidenceType.LOG,
        source="loki",
        summary="logs evidence collected",
        content_json=content_json,
        content_sha256=content_sha256,
        confidence=1.0,
        collected_at=NOW,
    )


def build_invocation(
    status: ToolInvocationStatus = ToolInvocationStatus.SUCCEEDED,
) -> ToolInvocation:
    """构造协调器测试使用的工具调用审计记录。"""
    succeeded = status is ToolInvocationStatus.SUCCEEDED
    return ToolInvocation(
        invocation_id="d" * 64,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id="collect.logs",
        operator_id="operator_001",
        trace_id="trc_001",
        tool_name="logs.query",
        tool_version="v1",
        risk_level=ToolRiskLevel.LOW,
        status=status,
        input_summary="payload_fields=10",
        input_sha256="a" * 64,
        output_summary="logs evidence collected" if succeeded else None,
        output_sha256="b" * 64 if succeeded else None,
        latency_ms=20,
        error_code=None if succeeded else "RuntimeError",
        started_at=NOW,
        ended_at=NOW + timedelta(milliseconds=20),
    )


def build_report() -> RCAReport:
    """构造协调器测试使用的基础报告。"""
    return RCAReport(
        report_id="f" * 64,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.UNDETERMINED,
        title="Root cause requires human review",
        summary="No verified root cause candidate was produced.",
        confidence=0.0,
        evidence_ids=("e" * 64,),
        evidence_type_counts=(("LOG", 1),),
        recommendations=("Review cited evidence.",),
        generator_name="deterministic-evidence-summary",
        generator_version="v1",
        generated_at=NOW,
    )


class FakeAgentWorkflow:
    """模拟立即成功、失败或阻塞的Agent端口。"""

    def __init__(
        self,
        error: Exception | None = None,
        block: bool = False,
        result: AgentWorkflowResult | None = None,
    ) -> None:
        self.error = error
        self.block = block
        self.result = result or AgentWorkflowResult()
        self.commands: list[ExecuteRCAWorkflowCommand] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def execute(
        self,
        command: ExecuteRCAWorkflowCommand,
    ) -> AgentWorkflowResult:
        self.commands.append(command)
        self.started.set()
        if self.block:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.error is not None:
            raise self.error
        return self.result


class FakeExecutionControl:
    """记录心跳和完成命令，并可注入异常。"""

    def __init__(
        self,
        heartbeat_error: Exception | None = None,
        completion_error: Exception | None = None,
    ) -> None:
        self.heartbeat_error = heartbeat_error
        self.completion_error = completion_error
        self.heartbeats: list[HeartbeatWorkflowRunCommand] = []
        self.completions: list[CompleteWorkflowRunCommand] = []
        self.heartbeat_called = asyncio.Event()

    async def heartbeat(
        self,
        command: HeartbeatWorkflowRunCommand,
    ) -> WorkflowHeartbeatResult:
        self.heartbeats.append(command)
        self.heartbeat_called.set()
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        return WorkflowHeartbeatResult(
            workflow_run_id=command.workflow_run_id,
            status=WorkflowRunStatus.RUNNING.value,
            lease_owner=command.worker_id,
            heartbeat_at=NOW + timedelta(seconds=30),
            lease_expires_at=NOW + timedelta(minutes=2),
            execution_attempts=1,
            trace_id="trc_001",
        )

    async def complete(
        self,
        command: CompleteWorkflowRunCommand,
    ) -> WorkflowCompletionResult:
        self.completions.append(command)
        if self.completion_error is not None:
            raise self.completion_error
        return WorkflowCompletionResult(
            workflow_run_id=command.workflow_run_id,
            status=command.target_status.value,
            ended_at=NOW + timedelta(minutes=1),
            execution_attempts=command.execution_attempt,
            is_duplicate=False,
            trace_id="trc_001",
        )


class TriggerHeartbeatWaiter:
    """立即触发一次心跳，之后等待协调器停止信号。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self,
        stop_event: asyncio.Event,
        timeout_seconds: float,
    ) -> bool:
        del timeout_seconds
        self.calls += 1
        if self.calls == 1:
            return False
        await stop_event.wait()
        return True


def build_coordinator(
    agent: FakeAgentWorkflow,
    control: FakeExecutionControl,
    *,
    heartbeat_interval: timedelta = timedelta(seconds=1),
    execution_timeout: timedelta = timedelta(seconds=10),
    waiter: TriggerHeartbeatWaiter | None = None,
) -> RCAExecutionCoordinator:
    """构造使用可控Agent、租约服务和时间配置的协调器。"""
    return RCAExecutionCoordinator(
        agent_workflow=agent,
        execution_control=control,
        config=RCAExecutionCoordinatorConfig(
            heartbeat_interval=heartbeat_interval,
            execution_timeout=execution_timeout,
        ),
        wait_for_stop=waiter,
    )


async def test_successful_agent_execution_commits_succeeded_status() -> None:
    """Agent正常返回后应使用同一执行代次写入成功终态。"""
    workflow_result = AgentWorkflowResult(
        evidence=(build_evidence(),),
        invocations=(build_invocation(),),
        report=build_report(),
    )
    agent = FakeAgentWorkflow(result=workflow_result)
    control = FakeExecutionControl()

    result = await build_coordinator(agent, control).execute(
        build_event(),
        build_claim(),
        "worker_001",
    )

    assert result.status is WorkflowRunStatus.SUCCEEDED
    assert result.timed_out is False
    assert result.agent_error is None
    assert agent.commands[0].incident_id == "inc_001"
    assert agent.commands[0].execution_attempt == 1
    assert control.completions[0].target_status is WorkflowRunStatus.SUCCEEDED
    assert control.completions[0].execution_attempt == 1
    assert control.completions[0].evidence == workflow_result.evidence
    assert control.completions[0].invocations == workflow_result.invocations
    assert control.completions[0].report == workflow_result.report


async def test_agent_failure_persists_partial_invocation_audit() -> None:
    """工具失败时协调器应保留部分调用记录并提交失败终态。"""
    partial_result = AgentWorkflowResult(
        evidence=(build_evidence(),),
        invocations=(build_invocation(ToolInvocationStatus.FAILED),),
    )
    agent = FakeAgentWorkflow(
        error=AgentWorkflowExecutionFailure(
            partial_result,
            RuntimeError("provider failed"),
        )
    )
    control = FakeExecutionControl()

    result = await build_coordinator(agent, control).execute(
        build_event(),
        build_claim(),
        "worker_001",
    )

    assert result.status is WorkflowRunStatus.FAILED
    assert result.agent_error == "RuntimeError: provider failed"
    assert control.completions[0].evidence == partial_result.evidence
    assert control.completions[0].invocations == partial_result.invocations


async def test_partial_agent_failure_redacts_error_summary() -> None:
    """带部分审计结果的Agent失败也不能返回原始敏感错误文本。"""
    partial_result = AgentWorkflowResult(
        evidence=(build_evidence(),),
        invocations=(build_invocation(ToolInvocationStatus.FAILED),),
    )
    agent = FakeAgentWorkflow(
        error=AgentWorkflowExecutionFailure(
            partial_result,
            RuntimeError("provider password=hunter2 token=secret-token"),
        )
    )
    control = FakeExecutionControl()

    result = await build_coordinator(agent, control).execute(
        build_event(),
        build_claim(),
        "worker_001",
    )

    assert result.status is WorkflowRunStatus.FAILED
    assert result.agent_error == (
        "RuntimeError: provider password=[REDACTED] token=[REDACTED]"
    )
    assert "hunter2" not in str(result.agent_error)
    assert "secret-token" not in str(result.agent_error)
    assert control.completions[0].evidence == partial_result.evidence


async def test_agent_error_is_recorded_as_failed_after_heartbeat_stops() -> None:
    """Agent业务异常应收口为失败，并返回有限错误摘要。"""
    agent = FakeAgentWorkflow(error=RuntimeError("tool execution failed"))
    control = FakeExecutionControl()

    result = await build_coordinator(agent, control).execute(
        build_event(),
        build_claim(),
        "worker_001",
    )

    assert result.status is WorkflowRunStatus.FAILED
    assert result.agent_error == "RuntimeError: tool execution failed"
    assert control.completions[0].target_status is WorkflowRunStatus.FAILED


async def test_agent_error_summary_is_redacted() -> None:
    """普通Agent异常返回给消息链路前必须脱敏。"""
    agent = FakeAgentWorkflow(
        error=RuntimeError("tool password=hunter2 token=secret-token")
    )
    control = FakeExecutionControl()

    result = await build_coordinator(agent, control).execute(
        build_event(),
        build_claim(),
        "worker_001",
    )

    assert result.status is WorkflowRunStatus.FAILED
    assert result.agent_error == (
        "RuntimeError: tool password=[REDACTED] token=[REDACTED]"
    )
    assert "hunter2" not in str(result.agent_error)
    assert "secret-token" not in str(result.agent_error)


async def test_execution_timeout_cancels_agent_and_commits_failed() -> None:
    """超过总执行时间时取消Agent，并在仍持有租约时写入失败。"""
    agent = FakeAgentWorkflow(block=True)
    control = FakeExecutionControl()
    coordinator = build_coordinator(
        agent,
        control,
        heartbeat_interval=timedelta(milliseconds=5),
        execution_timeout=timedelta(milliseconds=20),
    )

    result = await coordinator.execute(
        build_event(),
        build_claim(),
        "worker_001",
    )

    assert agent.cancelled is True
    assert result.status is WorkflowRunStatus.FAILED
    assert result.timed_out is True
    assert result.agent_error == "TimeoutError: Agent execution timed out"


async def test_heartbeat_failure_cancels_agent_without_completing() -> None:
    """无法续租时必须停止Agent，旧执行器不得写成功或失败终态。"""
    agent = FakeAgentWorkflow(block=True)
    control = FakeExecutionControl(
        heartbeat_error=WorkflowLeaseLostError("lease lost"),
    )
    waiter = TriggerHeartbeatWaiter()
    coordinator = build_coordinator(agent, control, waiter=waiter)

    with pytest.raises(WorkflowLeaseLostError, match="lease lost"):
        await coordinator.execute(
            build_event(),
            build_claim(),
            "worker_001",
        )

    assert agent.cancelled is True
    assert len(control.heartbeats) == 1
    assert control.completions == []


async def test_completion_failure_propagates_for_message_retry() -> None:
    """终态持久化失败时保留异常，让Kafka层回退offset。"""
    agent = FakeAgentWorkflow()
    control = FakeExecutionControl(
        completion_error=PersistenceError("database unavailable"),
    )

    with pytest.raises(PersistenceError, match="database unavailable"):
        await build_coordinator(agent, control).execute(
            build_event(),
            build_claim(),
            "worker_001",
        )


async def test_outer_cancellation_cleans_children_without_completion() -> None:
    """Runtime强制取消时清理Agent和心跳，并保留消息重放机会。"""
    agent = FakeAgentWorkflow(block=True)
    control = FakeExecutionControl()
    coordinator = build_coordinator(agent, control)
    task = asyncio.create_task(
        coordinator.execute(build_event(), build_claim(), "worker_001")
    )
    await agent.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert agent.cancelled is True
    assert control.completions == []


@pytest.mark.parametrize(
    "claim",
    [
        build_claim(acquired=False),
        build_claim(lease_owner="worker_002"),
        build_claim(status=WorkflowRunStatus.PENDING.value),
        build_claim(workflow_run_id="wfr_other"),
    ],
)
async def test_invalid_claim_is_rejected_before_agent_start(
    claim: WorkflowClaimResult,
) -> None:
    """不属于当前Worker的租约不能进入Agent端口。"""
    agent = FakeAgentWorkflow()
    coordinator = build_coordinator(agent, FakeExecutionControl())

    with pytest.raises(AppValidationError):
        await coordinator.execute(build_event(), claim, "worker_001")

    assert agent.commands == []


async def test_invalid_worker_id_is_rejected_before_agent_start() -> None:
    """直接调用协调器时也不能绕过统一Worker身份校验。"""
    agent = FakeAgentWorkflow()
    coordinator = build_coordinator(agent, FakeExecutionControl())

    with pytest.raises(AppValidationError, match="worker_id"):
        await coordinator.execute(
            build_event(),
            build_claim(lease_owner="worker\nforged"),
            "worker\nforged",
        )

    assert agent.commands == []
