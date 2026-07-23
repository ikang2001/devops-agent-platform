from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.tools.definition import ToolDefinition


def test_tool_definition_basic_structure() -> None:
    definition = ToolDefinition(
        tool_name="logs.query",
        version="v1",
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=3000,
        permission_tags=("logs:read",),
    )

    assert definition.tool_name == "logs.query"
    assert definition.version == "v1"
    assert definition.risk_level is ToolRiskLevel.LOW
    assert definition.timeout_ms == 3000
    assert definition.permission_tags == ("logs:read",)

