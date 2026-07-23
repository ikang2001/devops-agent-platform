from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from devops_agent_platform.application.commands.workflow_execution import (
    ClaimWorkflowRunCommand,
    CompleteWorkflowRunCommand,
    HeartbeatWorkflowRunCommand,
)
from devops_agent_platform.application.exceptions import WorkflowLeaseLostError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class WorkflowClaimConfig:
    """工作流执行租约配置。"""

    lease_duration: timedelta = timedelta(minutes=2)

    def __post_init__(self) -> None:
        """限制租约长度，防止零租约抖动或超长时间无法接管。"""
        if not isinstance(self.lease_duration, timedelta):
            raise AppValidationError("lease_duration must be a timedelta")
        if not timedelta(seconds=10) <= self.lease_duration <= timedelta(hours=1):
            raise AppValidationError(
                "lease_duration must be between 10 seconds and 1 hour"
            )


@dataclass(frozen=True)
class WorkflowClaimResult:
    """执行器抢占工作流后的稳定应用结果。"""

    workflow_run_id: str
    status: str
    acquired: bool
    lease_owner: str | None
    lease_expires_at: datetime | None
    execution_attempts: int
    trace_id: str


@dataclass(frozen=True)
class WorkflowHeartbeatResult:
    """执行租约成功续期后的稳定应用结果。"""

    workflow_run_id: str
    status: str
    lease_owner: str
    heartbeat_at: datetime
    lease_expires_at: datetime
    execution_attempts: int
    trace_id: str


@dataclass(frozen=True)
class WorkflowCompletionResult:
    """工作流成功或失败收口后的稳定应用结果。"""

    workflow_run_id: str
    status: str
    ended_at: datetime
    execution_attempts: int
    is_duplicate: bool
    trace_id: str


class WorkflowExecutionApplicationService:
    """通过短事务为RCA工作流分配有期限的执行权。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        config: WorkflowClaimConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._config = config or WorkflowClaimConfig()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def claim(
        self,
        command: ClaimWorkflowRunCommand,
    ) -> WorkflowClaimResult:
        """抢占待执行任务或接管租约已过期的运行任务。

        返回 ``acquired=False`` 表示消息属于重复投递、任务正在被其他执行器
        处理或已经进入终态，调用方不应启动第二个Agent。
        """
        now = self._now()
        lease_expires_at = now + self._config.lease_duration
        async with self._unit_of_work_factory() as unit_of_work:
            claimed = await unit_of_work.workflow_runs.claim_for_execution(
                tenant_id=command.tenant_id,
                workflow_run_id=command.workflow_run_id,
                worker_id=command.worker_id,
                now=now,
                lease_expires_at=lease_expires_at,
            )
            if claimed is not None:
                await unit_of_work.commit()
                return self._result(claimed, acquired=True)

            existing = await unit_of_work.workflow_runs.get_by_id(
                command.tenant_id,
                command.workflow_run_id,
            )
            if existing is None:
                raise ResourceNotFound("Workflow run not found")
            return self._result(existing, acquired=False)

    async def heartbeat(
        self,
        command: HeartbeatWorkflowRunCommand,
    ) -> WorkflowHeartbeatResult:
        """为当前执行器续租，失败时明确要求调用方停止执行。"""
        now = self._now()
        lease_expires_at = now + self._config.lease_duration
        async with self._unit_of_work_factory() as unit_of_work:
            renewed = (
                await unit_of_work.workflow_runs.renew_execution_lease(
                    tenant_id=command.tenant_id,
                    workflow_run_id=command.workflow_run_id,
                    worker_id=command.worker_id,
                    now=now,
                    lease_expires_at=lease_expires_at,
                )
            )
            if renewed is not None:
                await unit_of_work.commit()
                return self._heartbeat_result(renewed)

            existing = await unit_of_work.workflow_runs.get_by_id(
                command.tenant_id,
                command.workflow_run_id,
            )
            if existing is None:
                raise ResourceNotFound("Workflow run not found")
            raise WorkflowLeaseLostError(
                "Workflow execution lease is no longer owned by this worker"
            )

    async def complete(
        self,
        command: CompleteWorkflowRunCommand,
    ) -> WorkflowCompletionResult:
        """由当前执行代次提交终态，并支持相同完成命令幂等重放。"""
        completed_at = self._now()
        async with self._unit_of_work_factory() as unit_of_work:
            completed = await unit_of_work.workflow_runs.complete_execution(
                tenant_id=command.tenant_id,
                workflow_run_id=command.workflow_run_id,
                worker_id=command.worker_id,
                execution_attempt=command.execution_attempt,
                target_status=command.target_status,
                completed_at=completed_at,
            )
            if completed is not None:
                self._validate_completion_artifacts(completed, command)
                for evidence in command.evidence:
                    await unit_of_work.evidence.save(evidence)
                for invocation in command.invocations:
                    await unit_of_work.tool_invocations.save(invocation)
                if command.report is not None:
                    await unit_of_work.rca_reports.save(command.report)
                await unit_of_work.commit()
                return self._completion_result(
                    completed,
                    is_duplicate=False,
                )

            existing = await unit_of_work.workflow_runs.get_by_id(
                command.tenant_id,
                command.workflow_run_id,
            )
            if existing is None:
                raise ResourceNotFound("Workflow run not found")
            if (
                existing.status is command.target_status
                and existing.execution_attempts == command.execution_attempt
            ):
                return self._completion_result(
                    existing,
                    is_duplicate=True,
                )
            raise WorkflowLeaseLostError(
                "Workflow completion was rejected by execution fencing"
            )

    @staticmethod
    def _validate_completion_artifacts(
        workflow_run: WorkflowRun,
        command: CompleteWorkflowRunCommand,
    ) -> None:
        """校验待落库产物全部属于被 fencing 接受的事故聚合。"""
        if any(
            item.incident_id != workflow_run.incident_id
            for item in command.evidence
        ):
            raise AppValidationError(
                "evidence incident_id does not match workflow run"
            )
        if any(
            item.incident_id != workflow_run.incident_id
            for item in command.invocations
        ):
            raise AppValidationError(
                "invocation incident_id does not match workflow run"
            )
        if (
            command.report is not None
            and command.report.incident_id != workflow_run.incident_id
        ):
            raise AppValidationError(
                "report incident_id does not match workflow run"
            )

    def _now(self) -> datetime:
        """读取带时区时钟，防止租约比较混入本地无时区时间。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return timezone-aware datetime")
        return value

    @staticmethod
    def _result(
        workflow_run: WorkflowRun,
        *,
        acquired: bool,
    ) -> WorkflowClaimResult:
        """构造不暴露幂等摘要和请求指纹的执行结果。"""
        return WorkflowClaimResult(
            workflow_run_id=workflow_run.workflow_run_id,
            status=workflow_run.status.value,
            acquired=acquired,
            lease_owner=workflow_run.lease_owner,
            lease_expires_at=workflow_run.lease_expires_at,
            execution_attempts=workflow_run.execution_attempts,
            trace_id=workflow_run.trace_id,
        )

    @staticmethod
    def _heartbeat_result(
        workflow_run: WorkflowRun,
    ) -> WorkflowHeartbeatResult:
        """从已续租聚合构造非空租约结果。"""
        if (
            workflow_run.lease_owner is None
            or workflow_run.heartbeat_at is None
            or workflow_run.lease_expires_at is None
        ):
            raise RuntimeError("Renewed workflow has an incomplete lease")
        return WorkflowHeartbeatResult(
            workflow_run_id=workflow_run.workflow_run_id,
            status=workflow_run.status.value,
            lease_owner=workflow_run.lease_owner,
            heartbeat_at=workflow_run.heartbeat_at,
            lease_expires_at=workflow_run.lease_expires_at,
            execution_attempts=workflow_run.execution_attempts,
            trace_id=workflow_run.trace_id,
        )

    @staticmethod
    def _completion_result(
        workflow_run: WorkflowRun,
        *,
        is_duplicate: bool,
    ) -> WorkflowCompletionResult:
        """从终态聚合构造完成结果。"""
        if workflow_run.ended_at is None:
            raise RuntimeError("Completed workflow has no ended_at")
        return WorkflowCompletionResult(
            workflow_run_id=workflow_run.workflow_run_id,
            status=workflow_run.status.value,
            ended_at=workflow_run.ended_at,
            execution_attempts=workflow_run.execution_attempts,
            is_duplicate=is_duplicate,
            trace_id=workflow_run.trace_id,
        )
