import hashlib
import hmac

import pytest
from pydantic import SecretStr

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    WebhookAuthenticationRequired,
)
from devops_agent_platform.infrastructure.auth import (
    HMACAlertWebhookAuthenticator,
    HMACWebhookAuthenticatorConfig,
)

SECRET = "alert-webhook-secret-with-at-least-32-bytes"
NOW = 1_783_000_000
BODY = b'{"tenant_id":"tenant_001"}'


def build_authenticator(
    *,
    tolerance_seconds: int = 300,
    max_body_bytes: int = 64 * 1024,
) -> HMACAlertWebhookAuthenticator:
    """构造使用固定时钟的真实HMAC认证器。"""
    return HMACAlertWebhookAuthenticator(
        HMACWebhookAuthenticatorConfig(
            secret=SecretStr(SECRET),
            tolerance_seconds=tolerance_seconds,
            max_body_bytes=max_body_bytes,
        ),
        clock=lambda: NOW,
    )


def sign(timestamp: str, body: bytes) -> str:
    """按公开Webhook契约生成测试签名。"""
    digest = hmac.new(
        SECRET.encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_authenticates_exact_raw_body() -> None:
    """签名必须覆盖时间戳和未经重新序列化的原始正文。"""
    timestamp = str(NOW)

    build_authenticator().verify(
        timestamp=timestamp,
        signature=sign(timestamp, BODY),
        body=BODY,
    )


@pytest.mark.parametrize(
    ("timestamp", "signature", "body"),
    [
        (None, None, BODY),
        ("not-a-number", "sha256=" + "0" * 64, BODY),
        (str(NOW), "SHA256=" + "0" * 64, BODY),
        (str(NOW), "sha256=" + "g" * 64, BODY),
        (str(NOW - 301), None, BODY),
        (str(NOW + 301), None, BODY),
        (str(NOW), None, b""),
    ],
)
def test_malformed_or_expired_credentials_are_rejected(
    timestamp: str | None,
    signature: str | None,
    body: bytes,
) -> None:
    """认证失败不区分原因，避免向调用方泄露验签细节。"""
    if signature is None and timestamp is not None:
        signature = sign(timestamp, body)

    with pytest.raises(WebhookAuthenticationRequired):
        build_authenticator().verify(
            timestamp=timestamp,
            signature=signature,
            body=body,
        )


def test_signature_fails_after_body_tampering() -> None:
    """合法签名不能复用于任何被改写的正文。"""
    timestamp = str(NOW)

    with pytest.raises(WebhookAuthenticationRequired):
        build_authenticator().verify(
            timestamp=timestamp,
            signature=sign(timestamp, BODY),
            body=BODY + b" ",
        )


def test_body_capacity_is_enforced_before_hashing() -> None:
    """超出配置容量的请求不得进入业务解析。"""
    timestamp = str(NOW)
    body = b"12345"

    with pytest.raises(WebhookAuthenticationRequired):
        build_authenticator(max_body_bytes=4).verify(
            timestamp=timestamp,
            signature=sign(timestamp, body),
            body=body,
        )


@pytest.mark.parametrize(
    "secret",
    [
        "short",
        "x" * 31,
        "x" * 32 + "\n",
        "x" * 32 + "\t",
        "x" * 32 + "\x7f",
        " " + "x" * 32,
    ],
)
def test_config_rejects_weak_or_header_unsafe_secrets(secret: str) -> None:
    """弱密钥和可污染配置输出的密钥必须在启动前失败。"""
    with pytest.raises(AppValidationError, match="secret"):
        HMACWebhookAuthenticatorConfig(secret=SecretStr(secret))


def test_config_repr_does_not_expose_secret() -> None:
    """调试配置时不能把Webhook共享密钥写入日志。"""
    config = HMACWebhookAuthenticatorConfig(secret=SecretStr(SECRET))

    assert SECRET not in repr(config)
