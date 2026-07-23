from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.domain.enums import (
    ToolInvocationStatus,
    ToolRiskLevel,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.tool_invocation import (
    ToolInvocation,
    build_tool_payload_sha256,
)

NOW = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)


def build_invocation(**overrides: object) -> ToolInvocation:
    """构造有效的成功工具调用审计对象。"""
    values: dict[str, object] = {
        "invocation_id": "a" * 64,
        "tenant_id": "tenant_001",
        "incident_id": "inc_001",
        "workflow_run_id": "wfr_001",
        "execution_attempt": 2,
        "step_id": "collect.logs",
        "operator_id": "operator_001",
        "trace_id": "trc_001",
        "tool_name": "logs.query",
        "tool_version": "v1",
        "risk_level": ToolRiskLevel.LOW,
        "status": ToolInvocationStatus.SUCCEEDED,
        "input_summary": "payload_fields=10",
        "input_sha256": "b" * 64,
        "output_summary": "logs evidence collected",
        "output_sha256": "c" * 64,
        "latency_ms": 25,
        "error_code": None,
        "started_at": NOW,
        "ended_at": NOW + timedelta(milliseconds=25),
    }
    values.update(overrides)
    return ToolInvocation(**values)  # type: ignore[arg-type]


def test_tool_invocation_accepts_consistent_success_and_failure() -> None:
    """成功与失败终态应分别满足互斥字段约束。"""
    success = build_invocation()
    failure = build_invocation(
        status=ToolInvocationStatus.FAILED,
        output_summary=None,
        output_sha256=None,
        error_code="ToolExecutionTimeoutError",
    )

    assert success.error_code is None
    assert failure.output_sha256 is None
    assert failure.error_code == "ToolExecutionTimeoutError"


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": ToolInvocationStatus.RUNNING},
        {"execution_attempt": True},
        {"latency_ms": -1},
        {"input_sha256": "not-a-digest"},
        {"started_at": NOW.replace(tzinfo=None)},
        {"ended_at": NOW - timedelta(seconds=1)},
        {
            "status": ToolInvocationStatus.SUCCEEDED,
            "error_code": "UnexpectedError",
        },
        {
            "status": ToolInvocationStatus.FAILED,
            "output_summary": None,
            "output_sha256": None,
            "error_code": None,
        },
    ],
)
def test_tool_invocation_rejects_inconsistent_audit_data(
    overrides: dict[str, object],
) -> None:
    """脏审计数据必须在进入仓储前被领域对象拒绝。"""
    with pytest.raises(AppValidationError):
        build_invocation(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"step_id": "collect.logs\nforged"},
        {"tool_name": "logs.query\tforged"},
        {"input_summary": "payload_fields=10\x7fforged"},
        {"input_summary": "payload_fields=10\u0000forged"},
        {"output_summary": "logs evidence\nforged"},
        {
            "status": ToolInvocationStatus.FAILED,
            "output_summary": None,
            "output_sha256": None,
            "error_code": "TimeoutError\x7fforged",
        },
    ],
)
def test_tool_invocation_rejects_control_characters_in_public_fields(
    overrides: dict[str, object],
) -> None:
    """公开工具调用审计字段不能携带不可见控制字符。"""
    with pytest.raises(AppValidationError, match="control characters"):
        build_invocation(**overrides)


def test_payload_digest_is_canonical_and_does_not_expose_payload() -> None:
    """键顺序不影响摘要，摘要结果本身不包含敏感原文。"""
    first = build_tool_payload_sha256({"token": "secret-value", "window": 15})
    second = build_tool_payload_sha256({"window": 15, "token": "secret-value"})

    assert first == second
    assert "secret-value" not in first
    assert len(first) == 64


def test_payload_digest_rejects_non_json_values() -> None:
    """不可序列化对象不能进入审计哈希流程。"""
    with pytest.raises(AppValidationError, match="JSON serializable"):
        build_tool_payload_sha256({"bad": object()})
