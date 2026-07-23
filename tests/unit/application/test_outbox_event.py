from datetime import UTC, datetime

import pytest

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import AppValidationError


def valid_values() -> dict[str, object]:
    """返回 Outbox 事件契约测试使用的有效字段。"""
    return {
        "event_id": "evt_001",
        "tenant_id": "tenant_001",
        "aggregate_type": "Incident",
        "aggregate_id": "inc_001",
        "event_type": "incident.created",
        "schema_version": 1,
        "payload": {"incident_id": "inc_001"},
        "occurred_at": datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        "trace_id": "trc_001",
    }


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("event_id", ""),
        ("tenant_id", " tenant_001"),
        ("aggregate_type", ""),
        ("aggregate_id", "x" * 65),
        ("event_type", ""),
        ("schema_version", 0),
        ("schema_version", True),
        ("payload", {"bad": object()}),
        ("payload", {"bad": float("nan")}),
        ("occurred_at", datetime(2026, 6, 27, 10, 0)),
        ("trace_id", ""),
    ],
)
def test_outbox_event_rejects_invalid_contract(
    field_name: str,
    field_value: object,
) -> None:
    values = valid_values()
    values[field_name] = field_value

    with pytest.raises(AppValidationError):
        OutboxEvent(**values)  # type: ignore[arg-type]


def test_outbox_event_rejects_payload_larger_than_64_kib() -> None:
    values = valid_values()
    values["payload"] = {"content": "x" * (64 * 1024)}

    with pytest.raises(AppValidationError, match="must not exceed"):
        OutboxEvent(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("event_id", "evt_001\nforged"),
        ("tenant_id", "tenant_001\tforged"),
        ("aggregate_type", "Incident\rforged"),
        ("aggregate_id", "inc_001\nforged"),
        ("event_type", "incident.created\tforged"),
        ("trace_id", "trc_001\x7fforged"),
        ("trace_id", "trc_001\rforged"),
    ],
)
def test_outbox_event_rejects_control_character_metadata(
    field_name: str,
    field_value: object,
) -> None:
    """Outbox 元数据会进入消息和索引，必须保持单行。"""
    values = valid_values()
    values[field_name] = field_value

    with pytest.raises(AppValidationError, match="control characters"):
        OutboxEvent(**values)  # type: ignore[arg-type]
