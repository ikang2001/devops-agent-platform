from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from devops_agent_platform.application.commands.workflow_execution import (
    ClaimWorkflowRunCommand,
)
from devops_agent_platform.application.exceptions import ResourceBusyError
from devops_agent_platform.application.messages.rca_requested import (
    RCARequestedEventV1,
)
from devops_agent_platform.application.services.rca_execution_coordinator import (
    RCAExecutionResult,
)
from devops_agent_platform.application.services.workflow_execution_service import (
    WorkflowClaimResult,
)
from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.identity import validate_worker_id


class RCARequestedDisposition(StrEnum):
    """消费处理器对消息作出的稳定处理决定。"""

    EXECUTED = "EXECUTED"
    IGNORED = "IGNORED"


@dataclass(frozen=True)
class RCARequestedHandlingResult:
    """消息完成抢占判断后的结果，供消费适配器决定后续动作。"""

    event_id: str
    workflow_run_id: str
    disposition: RCARequestedDisposition
    workflow_status: str
    execution_attempt: int
    trace_id: str
    timed_out: bool = False
    agent_error: str | None = None


class WorkflowClaimServicePort(Protocol):
    """消息处理器实际依赖的最小工作流抢占能力。"""

    async def claim(
        self,
        command: ClaimWorkflowRunCommand,
    ) -> WorkflowClaimResult:
        """尝试获得工作流执行租约。"""
        ...


class RCAExecutionCoordinatorPort(Protocol):
    """消息处理器依赖的抢占后Agent执行协调能力。"""

    async def execute(
        self,
        event: RCARequestedEventV1,
        claim: WorkflowClaimResult,
        worker_id: str,
    ) -> RCAExecutionResult:
        """执行工作流并完成数据库终态收口。"""
        ...


class RCARequestedMessageHandler:
    """校验后的rca.requested消息到工作流抢占用例的适配器。"""

    def __init__(
        self,
        workflow_execution_service: WorkflowClaimServicePort,
        execution_coordinator: RCAExecutionCoordinatorPort,
        worker_id: str,
    ) -> None:
        validate_worker_id(worker_id)
        self._workflow_execution_service = workflow_execution_service
        self._execution_coordinator = execution_coordinator
        self._worker_id = worker_id

    async def handle(
        self,
        event: RCARequestedEventV1,
    ) -> RCARequestedHandlingResult:
        """尝试获得执行权；重复投递只返回忽略决定，不启动第二次执行。"""
        claim = await self._workflow_execution_service.claim(
            ClaimWorkflowRunCommand(
                tenant_id=event.tenant_id,
                workflow_run_id=event.workflow_run_id,
                worker_id=self._worker_id,
            )
        )
        if not claim.acquired:
            if claim.status in {
                WorkflowRunStatus.SUCCEEDED.value,
                WorkflowRunStatus.FAILED.value,
                WorkflowRunStatus.CANCELED.value,
            }:
                return RCARequestedHandlingResult(
                    event_id=event.event_id,
                    workflow_run_id=claim.workflow_run_id,
                    disposition=RCARequestedDisposition.IGNORED,
                    workflow_status=claim.status,
                    execution_attempt=claim.execution_attempts,
                    trace_id=claim.trace_id,
                )
            raise ResourceBusyError(
                "Workflow is active but not completed; retry the message"
            )

        execution = await self._execution_coordinator.execute(
            event,
            claim,
            self._worker_id,
        )
        return RCARequestedHandlingResult(
            event_id=event.event_id,
            workflow_run_id=execution.workflow_run_id,
            disposition=RCARequestedDisposition.EXECUTED,
            workflow_status=execution.status.value,
            execution_attempt=execution.execution_attempt,
            trace_id=execution.trace_id,
            timed_out=execution.timed_out,
            agent_error=execution.agent_error,
        )
