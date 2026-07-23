import pytest

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
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


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("tool_name", ""),
        ("tool_name", "Logs.Query"),
        ("tool_name", "logs query"),
        ("version", ""),
        ("version", "v1 beta"),
        ("timeout_ms", 0),
        ("timeout_ms", 300_001),
        ("timeout_ms", True),
        ("permission_tags", ("logs:read", "logs:read")),
        ("permission_tags", ("logs:read ",)),
    ],
)
def test_tool_definition_rejects_invalid_metadata(
    field_name: str,
    field_value: object,
) -> None:
    values = {
        "tool_name": "logs.query",
        "version": "v1",
        "risk_level": ToolRiskLevel.LOW,
        "timeout_ms": 3000,
        "permission_tags": ("logs:read",),
    }
    values[field_name] = field_value

    with pytest.raises(AppValidationError):
        ToolDefinition(**values)  # type: ignore[arg-type]
