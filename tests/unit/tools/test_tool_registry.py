from concurrent.futures import ThreadPoolExecutor

import pytest

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.registry import ToolRegistry


def build_definition(
    tool_name: str = "logs.query",
    version: str = "v1",
) -> ToolDefinition:
    return ToolDefinition(
        tool_name=tool_name,
        version=version,
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=3000,
        permission_tags=("logs:read",),
    )


def test_register_and_get_exact_tool_version() -> None:
    definition = build_definition()
    registry = ToolRegistry()

    registry.register(definition)

    assert registry.get("logs.query", "v1") is definition


def test_same_tool_can_register_multiple_versions() -> None:
    first_version = build_definition(version="v1")
    second_version = build_definition(version="v2")
    registry = ToolRegistry((second_version, first_version))

    assert registry.list_tools() == [first_version, second_version]


def test_duplicate_registration_does_not_overwrite_original_definition() -> None:
    original = build_definition()
    duplicate = build_definition()
    registry = ToolRegistry((original,))

    with pytest.raises(ConflictError) as exc_info:
        registry.register(duplicate)

    assert exc_info.value.code == "CONFLICT"
    assert registry.get("logs.query", "v1") is original


def test_get_unknown_tool_requires_explicit_error_handling() -> None:
    registry = ToolRegistry()

    with pytest.raises(ResourceNotFound) as exc_info:
        registry.get("metrics.query", "v1")

    assert exc_info.value.code == "RESOURCE_NOT_FOUND"


@pytest.mark.parametrize(
    ("tool_name", "version"),
    [
        ("", "v1"),
        (" logs.query", "v1"),
        ("logs.query", ""),
        ("logs.query", "v1 "),
    ],
)
def test_get_rejects_ambiguous_lookup_keys(tool_name: str, version: str) -> None:
    registry = ToolRegistry()

    with pytest.raises(AppValidationError):
        registry.get(tool_name, version)


def test_concurrent_duplicate_registration_has_single_winner() -> None:
    registry = ToolRegistry()
    definitions = [build_definition() for _ in range(16)]

    def register(definition: ToolDefinition) -> bool:
        try:
            registry.register(definition)
        except ConflictError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(register, definitions))

    assert results.count(True) == 1
    assert results.count(False) == 15
    assert registry.list_tools() == [registry.get("logs.query", "v1")]
