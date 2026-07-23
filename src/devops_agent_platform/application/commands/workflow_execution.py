from dataclasses import dataclass

from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation


@dataclass(frozen=True)
class ClaimWorkflowRunCommand:
    """消费侧请求获得工作流执行权的应用命令。"""

    tenant_id: str
    workflow_run_id: str
    worker_id: str

    def __post_init__(self) -> None:
        """校验消息适配器传入的租户、任务和消费者标识。"""
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("workflow_run_id", self.workflow_run_id, 64)
        validate_worker_id(self.worker_id)

    @staticmethod
    def _validate_text(field_name: str, value: str, maximum: int) -> None:
        """拒绝空值、超长值和首尾空白，避免污染索引与日志。"""
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {maximum}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )


@dataclass(frozen=True)
class HeartbeatWorkflowRunCommand:
    """运行中的执行器请求延长当前租约的应用命令。"""

    tenant_id: str
    workflow_run_id: str
    worker_id: str

    def __post_init__(self) -> None:
        """校验消费者进程传入的租约身份字段。"""
        ClaimWorkflowRunCommand._validate_text(
            "tenant_id",
            self.tenant_id,
            128,
        )
        ClaimWorkflowRunCommand._validate_text(
            "workflow_run_id",
            self.workflow_run_id,
            64,
        )
        validate_worker_id(self.worker_id)


@dataclass(frozen=True)
class CompleteWorkflowRunCommand:
    """执行器提交成功或失败终态的应用命令。"""

    tenant_id: str
    workflow_run_id: str
    worker_id: str
    execution_attempt: int
    target_status: WorkflowRunStatus
    evidence: tuple[Evidence, ...] = ()
    invocations: tuple[ToolInvocation, ...] = ()
    report: RCAReport | None = None

    def __post_init__(self) -> None:
        """校验身份字段、fencing代次和允许写入的终态。"""
        ClaimWorkflowRunCommand._validate_text(
            "tenant_id",
            self.tenant_id,
            128,
        )
        ClaimWorkflowRunCommand._validate_text(
            "workflow_run_id",
            self.workflow_run_id,
            64,
        )
        validate_worker_id(self.worker_id)
        if (
            isinstance(self.execution_attempt, bool)
            or not isinstance(self.execution_attempt, int)
            or self.execution_attempt < 1
        ):
            raise AppValidationError("execution_attempt must be a positive integer")
        if self.target_status not in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
        }:
            raise AppValidationError("target_status must be SUCCEEDED or FAILED")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, Evidence) for item in self.evidence
        ):
            raise AppValidationError("evidence must be a tuple of Evidence")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise AppValidationError("evidence IDs must be unique")
        for item in self.evidence:
            if item.tenant_id != self.tenant_id:
                raise AppValidationError("evidence tenant_id does not match")
            if item.workflow_run_id != self.workflow_run_id:
                raise AppValidationError("evidence workflow_run_id does not match")
            if item.execution_attempt != self.execution_attempt:
                raise AppValidationError("evidence execution_attempt does not match")
        if not isinstance(self.invocations, tuple) or not all(
            isinstance(item, ToolInvocation) for item in self.invocations
        ):
            raise AppValidationError("invocations must be a tuple of ToolInvocation")
        invocation_ids = [item.invocation_id for item in self.invocations]
        if len(invocation_ids) != len(set(invocation_ids)):
            raise AppValidationError("invocation IDs must be unique")
        invocation_steps = [item.step_id for item in self.invocations]
        if len(invocation_steps) != len(set(invocation_steps)):
            raise AppValidationError("invocation step IDs must be unique")
        for item in self.invocations:
            if item.tenant_id != self.tenant_id:
                raise AppValidationError("invocation tenant_id does not match")
            if item.workflow_run_id != self.workflow_run_id:
                raise AppValidationError("invocation workflow_run_id does not match")
            if item.execution_attempt != self.execution_attempt:
                raise AppValidationError("invocation execution_attempt does not match")
        if self.report is not None:
            if not isinstance(self.report, RCAReport):
                raise AppValidationError("report must be an RCAReport or None")
            if self.target_status is not WorkflowRunStatus.SUCCEEDED:
                raise AppValidationError("report requires SUCCEEDED target_status")
            if self.report.tenant_id != self.tenant_id:
                raise AppValidationError("report tenant_id does not match")
            if self.report.workflow_run_id != self.workflow_run_id:
                raise AppValidationError("report workflow_run_id does not match")
            if self.report.execution_attempt != self.execution_attempt:
                raise AppValidationError("report execution_attempt does not match")
            if not set(self.report.evidence_ids).issubset(set(evidence_ids)):
                raise AppValidationError(
                    "report references evidence outside completion command"
                )


@dataclass(frozen=True)
class ExecuteRCAWorkflowCommand:
    """交给Agent端口执行的、已经通过数据库抢占的工作流命令。"""

    tenant_id: str
    workflow_run_id: str
    incident_id: str
    operator_id: str
    worker_id: str
    execution_attempt: int
    trace_id: str

    def __post_init__(self) -> None:
        """校验数据库身份、执行器fencing代次和链路字段。"""
        fields = (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("incident_id", self.incident_id, 64),
            ("operator_id", self.operator_id, 128),
            ("trace_id", self.trace_id, 128),
        )
        for field_name, value, maximum in fields:
            ClaimWorkflowRunCommand._validate_text(
                field_name,
                value,
                maximum,
            )
        validate_worker_id(self.worker_id)
        if (
            isinstance(self.execution_attempt, bool)
            or not isinstance(self.execution_attempt, int)
            or self.execution_attempt < 1
        ):
            raise AppValidationError("execution_attempt must be a positive integer")
