import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from typing import Protocol

from devops_agent_platform.application.commands.workflow_execution import (
    CompleteWorkflowRunCommand,
    ExecuteRCAWorkflowCommand,
    HeartbeatWorkflowRunCommand,
)
from devops_agent_platform.application.messages.rca_requested import (
    RCARequestedEventV1,
)
from devops_agent_platform.application.services.workflow_execution_service import (
    WorkflowClaimResult,
    WorkflowCompletionResult,
    WorkflowHeartbeatResult,
)
from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.workflow import (
    AgentWorkflowExecutionFailure,
    AgentWorkflowPort,
    AgentWorkflowResult,
)
from devops_agent_platform.tools.sanitization import redact_sensitive_text

WaitForStop = Callable[[asyncio.Event, float], Awaitable[bool]]
logger = logging.getLogger(__name__)


class RCAExecutionObserver(Protocol):
    """记录一次已经成功收口的 RCA 执行指标。"""

    def observe_rca(
        self,
        *,
        outcome: str,
        duration_seconds: float,
        failed: bool = False,
        partial: bool = False,
    ) -> None:
        """记录低基数 RCA 执行指标。"""
        ...


class WorkflowExecutionControlPort(Protocol):
    """协调器依赖的租约续期和终态收口能力。"""

    async def heartbeat(
        self,
        command: HeartbeatWorkflowRunCommand,
    ) -> WorkflowHeartbeatResult:
        """续期当前执行租约。"""
        ...

    async def complete(
        self,
        command: CompleteWorkflowRunCommand,
    ) -> WorkflowCompletionResult:
        """提交当前执行代次的成功或失败终态。"""
        ...


@dataclass(frozen=True)
class RCAExecutionCoordinatorConfig:
    """Agent执行超时和租约心跳间隔配置。"""

    heartbeat_interval: timedelta = timedelta(seconds=30)
    execution_timeout: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        """确保时间配置为正，且执行窗口足以产生至少一次心跳。"""
        if (
            not isinstance(self.heartbeat_interval, timedelta)
            or self.heartbeat_interval <= timedelta(0)
        ):
            raise AppValidationError("heartbeat_interval must be positive")
        if (
            not isinstance(self.execution_timeout, timedelta)
            or self.execution_timeout <= timedelta(0)
        ):
            raise AppValidationError("execution_timeout must be positive")
        if self.heartbeat_interval >= self.execution_timeout:
            raise AppValidationError(
                "heartbeat_interval must be shorter than execution_timeout"
            )


@dataclass(frozen=True)
class RCAExecutionResult:
    """Agent执行及数据库终态提交完成后的结果。"""

    workflow_run_id: str
    status: WorkflowRunStatus
    execution_attempt: int
    timed_out: bool
    agent_error: str | None
    trace_id: str


class RCAExecutionCoordinator:
    """编排Agent执行、周期心跳、失权取消和终态提交。"""

    def __init__(
        self,
        agent_workflow: AgentWorkflowPort,
        execution_control: WorkflowExecutionControlPort,
        config: RCAExecutionCoordinatorConfig | None = None,
        wait_for_stop: WaitForStop | None = None,
        observer: RCAExecutionObserver | None = None,
    ) -> None:
        self._agent_workflow = agent_workflow
        self._execution_control = execution_control
        self._config = config or RCAExecutionCoordinatorConfig()
        self._wait_for_stop = wait_for_stop or self._default_wait_for_stop
        self._observer = observer

    async def execute(
        self,
        event: RCARequestedEventV1,
        claim: WorkflowClaimResult,
        worker_id: str,
    ) -> RCAExecutionResult:
        """执行已抢占任务，并在拥有有效租约时提交最终状态。"""
        started = perf_counter()
        self._validate_claim(event, claim, worker_id)
        agent_command = ExecuteRCAWorkflowCommand(
            tenant_id=event.tenant_id,
            workflow_run_id=event.workflow_run_id,
            incident_id=event.incident_id,
            operator_id=event.operator_id,
            worker_id=worker_id,
            execution_attempt=claim.execution_attempts,
            trace_id=claim.trace_id,
        )
        workflow_result, agent_error, timed_out = await self._run_with_heartbeat(
            agent_command
        )
        target_status = (
            WorkflowRunStatus.SUCCEEDED
            if agent_error is None and not timed_out
            else WorkflowRunStatus.FAILED
        )
        completion = await self._execution_control.complete(
            CompleteWorkflowRunCommand(
                tenant_id=event.tenant_id,
                workflow_run_id=event.workflow_run_id,
                worker_id=worker_id,
                execution_attempt=claim.execution_attempts,
                target_status=target_status,
                evidence=(
                    workflow_result.evidence
                    if workflow_result is not None
                    else ()
                ),
                invocations=(
                    workflow_result.invocations
                    if workflow_result is not None
                    else ()
                ),
                report=(
                    workflow_result.report
                    if workflow_result is not None
                    and target_status is WorkflowRunStatus.SUCCEEDED
                    else None
                ),
            )
        )
        result = RCAExecutionResult(
            workflow_run_id=completion.workflow_run_id,
            status=WorkflowRunStatus(completion.status),
            execution_attempt=completion.execution_attempts,
            timed_out=timed_out,
            agent_error=(
                self._safe_error_message(agent_error)
                if agent_error is not None
                else None
            ),
            trace_id=completion.trace_id,
        )
        self._observe_result(result, workflow_result, perf_counter() - started)
        return result

    def _observe_result(
        self,
        result: RCAExecutionResult,
        workflow_result: AgentWorkflowResult | None,
        duration_seconds: float,
    ) -> None:
        if self._observer is None:
            return
        outcome = (
            "succeeded"
            if result.status is WorkflowRunStatus.SUCCEEDED
            else "failed"
        )
        partial = bool(
            workflow_result is not None
            and workflow_result.report is not None
            and "Partial collection:" in workflow_result.report.summary
        )
        if partial and outcome == "succeeded":
            outcome = "partial"
        try:
            self._observer.observe_rca(
                outcome=outcome,
                duration_seconds=duration_seconds,
                failed=outcome == "failed",
                partial=partial,
            )
        except Exception:
            # 观测失败不能改变已经持久化的 RCA 终态，也不能触发消息重试。
            logger.warning("记录 RCA 执行指标失败")

    async def _run_with_heartbeat(
        self,
        command: ExecuteRCAWorkflowCommand,
    ) -> tuple[AgentWorkflowResult | None, Exception | None, bool]:
        """并行运行Agent和心跳，优先处理租约异常与外层取消。"""
        stop_event = asyncio.Event()
        agent_task = asyncio.create_task(
            self._agent_workflow.execute(command),
            name=f"rca-agent-{command.workflow_run_id}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(command, stop_event),
            name=f"rca-heartbeat-{command.workflow_run_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {agent_task, heartbeat_task},
                timeout=self._config.execution_timeout.total_seconds(),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await self._cancel_task(agent_task)
                return None, TimeoutError("Agent execution timed out"), True

            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                await self._cancel_task(agent_task)
                if heartbeat_error is not None:
                    raise heartbeat_error
                raise RuntimeError("Heartbeat loop stopped unexpectedly")

            try:
                workflow_result = await agent_task
            except AgentWorkflowExecutionFailure as exc:
                return exc.result, exc.cause, False
            except Exception as exc:
                return None, exc, False
            return workflow_result, None, False
        except asyncio.CancelledError:
            await self._cancel_task(agent_task)
            await self._cancel_task(heartbeat_task)
            raise
        finally:
            stop_event.set()
            await heartbeat_task

    async def _heartbeat_loop(
        self,
        command: ExecuteRCAWorkflowCommand,
        stop_event: asyncio.Event,
    ) -> None:
        """按固定间隔续租；任何异常交由协调器取消Agent。"""
        while not stop_event.is_set():
            stopped = await self._wait_for_stop(
                stop_event,
                self._config.heartbeat_interval.total_seconds(),
            )
            if stopped or stop_event.is_set():
                return
            await self._execution_control.heartbeat(
                HeartbeatWorkflowRunCommand(
                    tenant_id=command.tenant_id,
                    workflow_run_id=command.workflow_run_id,
                    worker_id=command.worker_id,
                )
            )

    @staticmethod
    def _validate_claim(
        event: RCARequestedEventV1,
        claim: WorkflowClaimResult,
        worker_id: str,
    ) -> None:
        """验证协调器收到的是当前Worker刚获得的同一工作流租约。"""
        ExecuteRCAWorkflowCommand(
            tenant_id=event.tenant_id,
            workflow_run_id=event.workflow_run_id,
            incident_id=event.incident_id,
            operator_id=event.operator_id,
            worker_id=worker_id,
            execution_attempt=claim.execution_attempts,
            trace_id=claim.trace_id,
        )
        if not claim.acquired:
            raise AppValidationError("execution coordinator requires acquired claim")
        if claim.workflow_run_id != event.workflow_run_id:
            raise AppValidationError("claim and event workflow_run_id do not match")
        if claim.lease_owner != worker_id:
            raise AppValidationError("claim lease_owner does not match worker_id")
        if claim.status != WorkflowRunStatus.RUNNING.value:
            raise AppValidationError("claimed workflow must be RUNNING")

    @staticmethod
    async def _cancel_task(task: asyncio.Task[None]) -> None:
        """取消未完成子任务并消费取消或失败结果。"""
        if task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    @staticmethod
    async def _default_wait_for_stop(
        stop_event: asyncio.Event,
        timeout_seconds: float,
    ) -> bool:
        """等待停止信号或下一次心跳时间。"""
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return False
        return True

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        """生成有限长度、已脱敏的单行Agent错误摘要。"""
        detail = " ".join(str(exc).splitlines()).strip()
        detail = redact_sensitive_text(detail)[0]
        message = type(exc).__name__
        if detail:
            message = f"{message}: {detail}"
        return message[:2048]
