from dataclasses import dataclass

from devops_agent_platform.domain.enums import ToolInvocationStatus


@dataclass
class ToolInvocation:
    """面向审计的一次外部工具调用记录。"""

    invocation_id: str
    incident_id: str
    tool_name: str
    tool_version: str
    status: ToolInvocationStatus
    input_summary: str
    output_summary: str | None
    latency_ms: int | None
    error_message: str | None
