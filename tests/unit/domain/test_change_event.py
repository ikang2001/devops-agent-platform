from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.domain.enums import ChangeEventStatus, ChangeType
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.change_event import (
    MAX_CHANGE_METADATA_BYTES,
    ChangeEvent,
    build_change_metadata,
)


def valid_values() -> dict[str, object]:
    """返回变更事件不变量测试使用的有效字段。"""
    started_at = datetime(2026, 8, 17, 14, 2, tzinfo=UTC)
    return {
        "change_event_id": "chg_001",
        "tenant_id": "tenant_001",
        "source": "argocd",
        "external_event_id": "deploy_payment_v2",
        "service_name": "payment-service",
        "resource_type": "deployment",
        "resource_id": "payment-service",
        "change_type": ChangeType.DEPLOYMENT,
        "status": ChangeEventStatus.SUCCEEDED,
        "version_before": "v1",
        "version_after": "v2",
        "operator_id": "deployment-bot",
        "summary": "payment-service upgraded from v1 to v2",
        "metadata_json": build_change_metadata(
            {"cluster": "minishop", "namespace": "default"}
        ),
        "started_at": started_at,
        "completed_at": started_at + timedelta(minutes=1),
        "created_at": started_at + timedelta(minutes=2),
        "request_hash": "a" * 64,
    }


def test_change_event_accepts_valid_deployment_fact() -> None:
    event = ChangeEvent(**valid_values())  # type: ignore[arg-type]

    assert event.change_type is ChangeType.DEPLOYMENT
    assert event.status is ChangeEventStatus.SUCCEEDED
    assert event.metadata_json == ('{"cluster":"minishop","namespace":"default"}')


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("change_event_id", ""),
        ("tenant_id", " tenant_001"),
        ("source", "argocd\nforged"),
        ("external_event_id", ""),
        ("service_name", "x" * 257),
        ("resource_type", "deployment\tforged"),
        ("resource_id", ""),
        ("change_type", "DEPLOYMENT"),
        ("status", "SUCCEEDED"),
        ("version_before", " v1"),
        ("operator_id", "operator\x7fforged"),
        ("summary", "deployment\nforged"),
        ("started_at", datetime(2026, 8, 17, 14, 2)),
        ("completed_at", datetime(2026, 8, 17, 14, 3)),
        ("created_at", datetime(2026, 8, 17, 14, 4)),
        ("request_hash", "not-a-sha256"),
    ],
)
def test_change_event_rejects_invalid_invariants(
    field_name: str,
    field_value: object,
) -> None:
    values = valid_values()
    values[field_name] = field_value

    with pytest.raises(AppValidationError):
        ChangeEvent(**values)  # type: ignore[arg-type]


def test_change_event_rejects_completed_at_before_started_at() -> None:
    values = valid_values()
    started_at = values["started_at"]
    assert isinstance(started_at, datetime)
    values["completed_at"] = started_at - timedelta(seconds=1)

    with pytest.raises(AppValidationError, match="completed_at must not precede"):
        ChangeEvent(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "metadata_json",
    [
        "[]",
        '{"duration":NaN}',
        '{"cluster": "minishop"}',
        "{invalid-json}",
    ],
)
def test_change_event_rejects_non_object_or_non_canonical_metadata(
    metadata_json: str,
) -> None:
    values = valid_values()
    values["metadata_json"] = metadata_json

    with pytest.raises(AppValidationError, match="metadata_json"):
        ChangeEvent(**values)  # type: ignore[arg-type]


def test_change_event_rejects_oversized_metadata() -> None:
    values = valid_values()
    values["metadata_json"] = build_change_metadata(
        {"payload": "x" * MAX_CHANGE_METADATA_BYTES},
        max_bytes=MAX_CHANGE_METADATA_BYTES * 2,
    )

    with pytest.raises(AppValidationError, match="metadata_json is too large"):
        ChangeEvent(**values)  # type: ignore[arg-type]


def test_build_change_metadata_is_deterministic_and_rejects_invalid_values() -> None:
    first = build_change_metadata({"version": 2, "service": "payment"})
    second = build_change_metadata({"service": "payment", "version": 2})

    assert first == second == '{"service":"payment","version":2}'
    with pytest.raises(AppValidationError, match="JSON serializable"):
        build_change_metadata({"unsupported": object()})
