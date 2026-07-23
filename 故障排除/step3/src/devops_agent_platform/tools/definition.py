from dataclasses import dataclass

from devops_agent_platform.domain.enums import ToolRiskLevel


@dataclass(frozen=True)
class ToolDefinition:
    """Agent 工作流调用工具前必须具备的工具元数据。"""

    tool_name: str
    version: str
    risk_level: ToolRiskLevel
    timeout_ms: int
    permission_tags: tuple[str, ...]
