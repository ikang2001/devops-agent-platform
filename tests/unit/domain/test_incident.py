from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.domain.enums import AlertSeverity, IncidentStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.incident import Incident


def valid_values() -> dict[str, object]:
    """返回每个测试独立修改的有效事故字段。"""
    now = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)
    return {
        "incident_id": "inc_001",
        "tenant_id": "tenant_001",
        "service_name": "checkout-api",
        "severity": AlertSeverity.CRITICAL,
        "status": IncidentStatus.OPEN,
        "title": "Checkout error rate is above threshold",
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("incident_id", ""),
        ("tenant_id", " tenant_001"),
        ("tenant_id", "tenant\n001"),
        ("tenant_id", "tenant\x7f001"),
        ("service_name", "x" * 257),
        ("service_name", "checkout-api\tforged"),
        ("title", ""),
        ("title", "Checkout error\nforged"),
        ("severity", "CRITICAL"),
        ("status", "OPEN"),
        ("created_at", datetime(2026, 6, 27, 10, 0)),
        ("version", 0),
        ("version", True),
    ],
)
def test_incident_rejects_invalid_invariants(
    field_name: str,
    field_value: object,
) -> None:
    values = valid_values()
    values[field_name] = field_value

    with pytest.raises(AppValidationError):
        Incident(**values)  # type: ignore[arg-type]


def test_incident_rejects_updated_time_before_creation() -> None:
    values = valid_values()
    values["updated_at"] = values["created_at"] - timedelta(seconds=1)  # type: ignore[operator]

    with pytest.raises(AppValidationError, match="must not be earlier"):
        Incident(**values)  # type: ignore[arg-type]


def test_active_incident_can_be_resolved_with_complete_metadata() -> None:
    """活动事故解决后应同步更新时间和完整审计事实。"""
    incident = Incident(**valid_values())  # type: ignore[arg-type]
    resolved_at = incident.updated_at + timedelta(minutes=5)

    incident.mark_resolved(
        resolved_by="admin_001",
        reason="Mitigation verified.",
        resolved_at=resolved_at,
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_resolution_001",
    )

    assert incident.status is IncidentStatus.RESOLVED
    assert incident.updated_at == resolved_at
    assert incident.resolved_at == resolved_at
    assert incident.resolved_by == "admin_001"
    assert incident.resolution_reason == "Mitigation verified."


def test_resolution_and_closure_reject_control_characters() -> None:
    """人工终态审计字段不能携带不可见控制字符。"""
    incident = Incident(**valid_values())  # type: ignore[arg-type]

    with pytest.raises(AppValidationError, match="control characters"):
        incident.mark_resolved(
            resolved_by="admin\x7f001",
            reason="Mitigation verified.\nforged",
            resolved_at=incident.updated_at + timedelta(minutes=5),
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            trace_id="trc_resolution_001",
        )

    incident.mark_resolved(
        resolved_by="admin_001",
        reason="Mitigation verified.",
        resolved_at=incident.updated_at + timedelta(minutes=5),
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_resolution_001",
    )
    with pytest.raises(AppValidationError, match="control characters"):
        incident.mark_closed(
            closed_by="admin\t001",
            reason="Post-incident checklist completed.",
            closed_at=incident.updated_at + timedelta(minutes=5),
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            trace_id="trc_closure_001",
        )


def test_terminal_incident_cannot_be_resolved_again() -> None:
    """终态事故必须由应用层幂等恢复，领域行为拒绝二次状态迁移。"""
    values = valid_values()
    values["status"] = IncidentStatus.RESOLVED
    incident = Incident(**values)  # type: ignore[arg-type]

    with pytest.raises(ConflictError, match="active"):
        incident.mark_resolved(
            resolved_by="admin_001",
            reason="Repeated request.",
            resolved_at=incident.updated_at,
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            trace_id="trc_resolution_001",
        )


def test_resolution_rejects_clock_regression_and_partial_metadata() -> None:
    """解决时间不能回拨，持久化恢复也不能接受半套解决字段。"""
    incident = Incident(**valid_values())  # type: ignore[arg-type]
    with pytest.raises(AppValidationError, match="resolved_at"):
        incident.mark_resolved(
            resolved_by="admin_001",
            reason="Mitigation verified.",
            resolved_at=incident.updated_at - timedelta(seconds=1),
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            trace_id="trc_resolution_001",
        )

    values = valid_values()
    values["status"] = IncidentStatus.RESOLVED
    values["resolved_by"] = "admin_001"
    with pytest.raises(AppValidationError, match="complete"):
        Incident(**values)  # type: ignore[arg-type]


def test_resolved_incident_can_be_closed_with_complete_metadata() -> None:
    """已解决事故关闭后应同步更新时间和完整审计事实。"""
    incident = Incident(**valid_values())  # type: ignore[arg-type]
    resolved_at = incident.updated_at + timedelta(minutes=5)
    closed_at = resolved_at + timedelta(minutes=10)
    incident.mark_resolved(
        resolved_by="admin_001",
        reason="Mitigation verified.",
        resolved_at=resolved_at,
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_resolution_001",
    )

    incident.mark_closed(
        closed_by="admin_001",
        reason="Post-incident checklist completed.",
        closed_at=closed_at,
        idempotency_key_hash="c" * 64,
        request_hash="d" * 64,
        trace_id="trc_closure_001",
    )

    assert incident.status is IncidentStatus.CLOSED
    assert incident.updated_at == closed_at
    assert incident.closed_at == closed_at
    assert incident.closed_by == "admin_001"
    assert incident.closure_reason == "Post-incident checklist completed."


def test_active_or_closed_incident_cannot_be_closed() -> None:
    """关闭动作只能从已解决状态进入，不能跳过解决或重复关闭。"""
    incident = Incident(**valid_values())  # type: ignore[arg-type]

    with pytest.raises(ConflictError, match="resolved"):
        incident.mark_closed(
            closed_by="admin_001",
            reason="Post-incident checklist completed.",
            closed_at=incident.updated_at,
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            trace_id="trc_closure_001",
        )


def test_closure_rejects_clock_regression_and_partial_metadata() -> None:
    """关闭时间不能回拨，持久化恢复也不能接受半套关闭字段。"""
    values = valid_values()
    values["status"] = IncidentStatus.RESOLVED
    values["resolved_by"] = "admin_001"
    values["resolution_reason"] = "Mitigation verified."
    values["resolved_at"] = values["updated_at"]
    values["resolution_idempotency_key_hash"] = "a" * 64
    values["resolution_request_hash"] = "b" * 64
    values["resolution_trace_id"] = "trc_resolution_001"
    incident = Incident(**values)  # type: ignore[arg-type]

    with pytest.raises(AppValidationError, match="closed_at"):
        incident.mark_closed(
            closed_by="admin_001",
            reason="Post-incident checklist completed.",
            closed_at=incident.updated_at - timedelta(seconds=1),
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            trace_id="trc_closure_001",
        )

    values["status"] = IncidentStatus.CLOSED
    values["closed_by"] = "admin_001"
    with pytest.raises(AppValidationError, match="complete"):
        Incident(**values)  # type: ignore[arg-type]
