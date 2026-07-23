import pytest

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.tools.sanitization import (
    EvidenceSanitizerConfig,
    redact_sensitive_text,
    sanitize_evidence_payload,
)


def test_recursive_sanitizer_redacts_sensitive_keys_and_text() -> None:
    """字段级和文本级凭据都应从持久化副本中移除。"""
    original = {
        "Authorization": "Bearer top-secret-token",
        "nested": {
            "db-password": "hunter2",
            "message": (
                "connect postgresql://alice:private@db.local/app "
                "email=alice@example.com"
            ),
        },
        "items": [{"client_secret": "private-value"}],
    }

    result = sanitize_evidence_payload(original)

    assert result.payload["Authorization"] == "[REDACTED]"
    assert result.payload["nested"]["db-password"] == "[REDACTED]"
    assert result.payload["items"][0]["client_secret"] == "[REDACTED]"
    assert "private" not in str(result.payload)
    assert "alice@example.com" not in str(result.payload)
    assert result.redacted_values >= 4
    # 清洗器必须构造副本，不能改变工具执行器仍在使用的返回对象。
    assert original["Authorization"] == "Bearer top-secret-token"
    assert original["nested"]["db-password"] == "hunter2"


def test_sanitizer_truncates_utf8_without_breaking_characters() -> None:
    """超长多字节文本应按字节安全截断。"""
    result = sanitize_evidence_payload(
        {"message": "故障" * 100},
        EvidenceSanitizerConfig(max_string_bytes=32),
    )

    message = result.payload["message"]
    assert len(message.encode("utf-8")) <= 32
    assert message.endswith("[TRUNCATED]")
    assert result.truncated_strings == 1


@pytest.mark.parametrize(
    ("payload", "config", "message"),
    [
        (
            {"level1": {"level2": {"value": "x"}}},
            EvidenceSanitizerConfig(max_depth=1),
            "maximum depth",
        ),
        (
            {"values": [1, 2, 3]},
            EvidenceSanitizerConfig(max_collection_items=2),
            "too many items",
        ),
        (
            {"a": 1, "b": 2},
            EvidenceSanitizerConfig(max_total_nodes=2),
            "too many nodes",
        ),
        (
            {1: "invalid"},
            EvidenceSanitizerConfig(),
            "keys must be strings",
        ),
    ],
)
def test_sanitizer_rejects_unsafe_structure(
    payload: dict,
    config: EvidenceSanitizerConfig,
    message: str,
) -> None:
    """超出结构边界时拒绝整份证据，不静默删除元素。"""
    with pytest.raises(AppValidationError, match=message):
        sanitize_evidence_payload(payload, config)


def test_redact_sensitive_text_covers_cookie_and_connection_uri() -> None:
    """通用文本脱敏应覆盖 Cookie 和带账号密码的连接地址。"""
    redacted, changed = redact_sensitive_text(
        "cookie=session-value dsn=mysql://root:db-pass@db.local/app"
    )

    assert changed is True
    assert "session-value" not in redacted
    assert "db-pass" not in redacted
    assert "root:" not in redacted


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_depth": True},
        {"max_collection_items": 0},
        {"max_total_nodes": 100_001},
        {"max_string_bytes": 65_537},
    ],
)
def test_sanitizer_config_rejects_unbounded_values(
    overrides: dict[str, object],
) -> None:
    """策略配置不能把容量保护调整为无界。"""
    with pytest.raises(AppValidationError):
        EvidenceSanitizerConfig(**overrides)  # type: ignore[arg-type]
