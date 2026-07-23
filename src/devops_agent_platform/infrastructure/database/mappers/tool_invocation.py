from devops_agent_platform.domain.enums import (
    ToolInvocationStatus,
    ToolRiskLevel,
)
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.infrastructure.database.models.tool_invocation import (
    ToolInvocationRecord,
)


class ToolInvocationMapper:
    """在工具调用领域对象与 ORM 记录之间做显式转换。"""

    @staticmethod
    def to_record(invocation: ToolInvocation) -> ToolInvocationRecord:
        """把完成态工具调用转换为待持久化记录。"""
        return ToolInvocationRecord(
            invocation_id=invocation.invocation_id,
            tenant_id=invocation.tenant_id,
            incident_id=invocation.incident_id,
            workflow_run_id=invocation.workflow_run_id,
            execution_attempt=invocation.execution_attempt,
            step_id=invocation.step_id,
            operator_id=invocation.operator_id,
            trace_id=invocation.trace_id,
            tool_name=invocation.tool_name,
            tool_version=invocation.tool_version,
            risk_level=invocation.risk_level.value,
            status=invocation.status.value,
            input_summary=invocation.input_summary,
            input_sha256=invocation.input_sha256,
            output_summary=invocation.output_summary,
            output_sha256=invocation.output_sha256,
            latency_ms=invocation.latency_ms,
            error_code=invocation.error_code,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
        )

    @staticmethod
    def to_domain(record: ToolInvocationRecord) -> ToolInvocation:
        """把 ORM 记录恢复为不依赖 SQLAlchemy 的领域对象。"""
        return ToolInvocation(
            invocation_id=record.invocation_id,
            tenant_id=record.tenant_id,
            incident_id=record.incident_id,
            workflow_run_id=record.workflow_run_id,
            execution_attempt=record.execution_attempt,
            step_id=record.step_id,
            operator_id=record.operator_id,
            trace_id=record.trace_id,
            tool_name=record.tool_name,
            tool_version=record.tool_version,
            risk_level=ToolRiskLevel(record.risk_level),
            status=ToolInvocationStatus(record.status),
            input_summary=record.input_summary,
            input_sha256=record.input_sha256,
            output_summary=record.output_summary,
            output_sha256=record.output_sha256,
            latency_ms=record.latency_ms,
            error_code=record.error_code,
            started_at=record.started_at,
            ended_at=record.ended_at,
        )
