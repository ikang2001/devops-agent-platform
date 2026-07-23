from datetime import UTC, datetime

import pytest

from devops_agent_platform.domain.enums import EvidenceType
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)

NOW = datetime(2026, 6, 29, 10, 0, tzinfo=UTC)


def build_evidence(**overrides: object) -> Evidence:
    """构造一个有效 Evidence，测试用例只覆盖自己关心的字段。"""
    content_json, content_sha256 = build_evidence_content(
        {
            "query": "{service='checkout-api'} |= 'error'",
            "sample_count": 12,
            "window_minutes": 15,
        }
    )
    values = {
        "evidence_id": "ev_001",
        "tenant_id": "tenant_001",
        "incident_id": "inc_001",
        "workflow_run_id": "wfr_001",
        "execution_attempt": 1,
        "step_id": "logs.query",
        "tool_name": "logs.query",
        "tool_version": "v1",
        "evidence_type": EvidenceType.LOG,
        "source": "loki",
        "summary": "15 分钟内发现 12 条 checkout-api error 日志",
        "content_json": content_json,
        "content_sha256": content_sha256,
        "confidence": 0.82,
        "collected_at": NOW,
    }
    values.update(overrides)
    return Evidence(**values)


def test_evidence_can_be_constructed_with_canonical_content() -> None:
    evidence = build_evidence()

    assert evidence.tenant_id == "tenant_001"
    assert evidence.execution_attempt == 1
    assert evidence.content_sha256


def test_build_evidence_content_is_deterministic() -> None:
    first_json, first_hash = build_evidence_content({"b": 2, "a": 1})
    second_json, second_hash = build_evidence_content({"a": 1, "b": 2})

    assert first_json == '{"a":1,"b":2}'
    assert first_hash == second_hash
    assert first_json == second_json


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_attempt": 0},
        {"confidence": 1.1},
        {"collected_at": datetime(2026, 6, 29, 10, 0)},
        {"content_sha256": "0" * 64},
        {"content_json": "[]"},
    ],
)
def test_evidence_rejects_invalid_core_fields(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(AppValidationError):
        build_evidence(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"step_id": "logs.query\nforged"},
        {"tool_name": "logs.query\tforged"},
        {"source": "loki\x7fforged"},
        {"source": "loki\nforged"},
        {"summary": "safe summary\u0000forged"},
    ],
)
def test_evidence_rejects_control_characters_in_public_fields(
    overrides: dict[str, object],
) -> None:
    """公开 Evidence 字段不能携带不可见控制字符。"""
    with pytest.raises(AppValidationError, match="control characters"):
        build_evidence(**overrides)


def test_build_evidence_content_rejects_oversized_payload() -> None:
    with pytest.raises(AppValidationError, match="too large"):
        build_evidence_content({"message": "x" * 20}, max_bytes=8)
