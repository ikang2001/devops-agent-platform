from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.domain.models.workflow_run import WorkflowRun


@dataclass(frozen=True)
class AgentWorkflowResult:
    """Agent 受控执行成功后的有限结果。

    当前只暴露结构化证据，不暴露任意中间状态。失败路径通过异常交给协调器收口，
    避免半成功结果被误当成完整 RCA 结论。
    """

    evidence: tuple[Evidence, ...] = ()
    invocations: tuple[ToolInvocation, ...] = ()
    report: RCAReport | None = None

    def __post_init__(self) -> None:
        """校验结果只包含不可变 Evidence 对象。"""
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, Evidence) for item in self.evidence
        ):
            raise TypeError("evidence must be a tuple of Evidence")
        if not isinstance(self.invocations, tuple) or not all(
            isinstance(item, ToolInvocation) for item in self.invocations
        ):
            raise TypeError(
                "invocations must be a tuple of ToolInvocation"
            )
        if self.report is not None and not isinstance(
            self.report,
            RCAReport,
        ):
            raise TypeError("report must be an RCAReport or None")


class AgentWorkflowExecutionFailure(Exception):
    """携带已完成调用审计记录的受控工作流失败。

    协调器应把 ``cause`` 作为业务失败原因，把 ``result`` 交给当前执行代次的
    完成事务。包装器不会把第三方异常文本写入审计表。
    """

    def __init__(
        self,
        result: AgentWorkflowResult,
        cause: Exception,
    ) -> None:
        super().__init__(str(cause))
        self.result = result
        self.cause = cause


class AgentWorkflowPort(Protocol):
    """执行已获得数据库租约的Agent工作流端口。"""

    async def execute(
        self,
        command: ExecuteRCAWorkflowCommand,
    ) -> AgentWorkflowResult:
        """执行固定RCA工作流；成功返回结构化证据，失败抛出异常。"""
        ...


class WorkflowRunRepositoryPort(Protocol):
    """RCA工作流运行记录的持久化端口。"""

    async def save(self, workflow_run: WorkflowRun) -> None:
        """新增或更新工作流运行记录。"""
        ...

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> WorkflowRun | None:
        """按租户和幂等键哈希查询。"""
        ...

    async def get_active_by_incident(
        self,
        tenant_id: str,
        incident_id: str,
    ) -> WorkflowRun | None:
        """查询事故当前唯一的待执行或运行中任务。"""
        ...

    async def get_by_id(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> WorkflowRun | None:
        """按租户和运行标识查询工作流。"""
        ...

    async def claim_for_execution(
        self,
        tenant_id: str,
        workflow_run_id: str,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRun | None:
        """原子抢占待执行或租约已过期的工作流。"""
        ...

    async def renew_execution_lease(
        self,
        tenant_id: str,
        workflow_run_id: str,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRun | None:
        """仅由当前所有者原子续期尚未过期的执行租约。"""
        ...

    async def complete_execution(
        self,
        tenant_id: str,
        workflow_run_id: str,
        worker_id: str,
        execution_attempt: int,
        target_status: WorkflowRunStatus,
        completed_at: datetime,
    ) -> WorkflowRun | None:
        """由当前有效执行代次原子写入成功或失败终态。"""
        ...
