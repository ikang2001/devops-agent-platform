from datetime import UTC, datetime

import pytest

from devops_agent_platform.domain.enums import AlertSeverity
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.alert import Alert


def valid_values() -> dict[str, object]:
    """返回告警不变量测试使用的有效字段。"""
    return {
        "alert_id": "alt_001",
        "tenant_id": "tenant_001",
        "source": "alertmanager",
        "service_name": "checkout-api",
        "severity": AlertSeverity.CRITICAL,
        "summary": "Checkout error rate is above threshold",
        "starts_at": datetime(2026, 6, 27, 8, 0, tzinfo=UTC),
        "fingerprint": "fp_checkout_error_rate",
        "external_event_id": "evt_001",
    }


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("alert_id", ""),
        ("tenant_id", " tenant_001"),
        ("source", ""),
        ("service_name", "x" * 257),
        ("severity", "CRITICAL"),
        ("summary", ""),
        ("summary", "Checkout error\x7fforged"),
        ("summary", "Checkout error\nforged"),
        ("source", "alertmanager\tforged"),
        ("source", "alertmanager\x7fforged"),
        ("starts_at", datetime(2026, 6, 27, 8, 0)),
        ("fingerprint", "fp_001 "),
        ("external_event_id", "evt_001\nforged"),
        ("external_event_id", ""),
    ],
)
def test_alert_rejects_invalid_invariants(
    field_name: str,
    field_value: object,
) -> None:
    values = valid_values()
    values[field_name] = field_value

    with pytest.raises(AppValidationError):
        Alert(**values)  # type: ignore[arg-type]
