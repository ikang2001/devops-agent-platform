import hashlib
import hmac
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import SecretStr

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    WebhookAuthenticationRequired,
)

WallClock = Callable[[], float]
_SIGNATURE_PATTERN = re.compile(r"sha256=([0-9a-f]{64})")


def _contains_ascii_control(value: str) -> bool:
    """共享密钥不能携带任何不可见ASCII控制字符。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


@dataclass(frozen=True)
class HMACWebhookAuthenticatorConfig:
    """外部Webhook HMAC认证的有限配置。"""

    secret: SecretStr = field(repr=False)
    tolerance_seconds: int = 300
    max_body_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        """在接收流量前拒绝弱密钥和无界参数。"""
        if not isinstance(self.secret, SecretStr):
            raise AppValidationError("webhook secret must be a SecretStr")
        secret = self.secret.get_secret_value()
        if (
            len(secret) < 32
            or secret != secret.strip()
            or _contains_ascii_control(secret)
        ):
            raise AppValidationError("webhook secret is invalid")
        if (
            isinstance(self.tolerance_seconds, bool)
            or not isinstance(self.tolerance_seconds, int)
            or not 10 <= self.tolerance_seconds <= 3600
        ):
            raise AppValidationError(
                "webhook tolerance_seconds must be between 10 and 3600"
            )
        if (
            isinstance(self.max_body_bytes, bool)
            or not isinstance(self.max_body_bytes, int)
            or not 1 <= self.max_body_bytes <= 1024 * 1024
        ):
            raise AppValidationError(
                "webhook max_body_bytes must be between 1 and 1048576"
            )


class HMACAlertWebhookAuthenticator:
    """使用时间戳和原始正文验证告警Webhook完整性。"""

    def __init__(
        self,
        config: HMACWebhookAuthenticatorConfig,
        *,
        clock: WallClock = time.time,
    ) -> None:
        if not isinstance(config, HMACWebhookAuthenticatorConfig):
            raise AppValidationError(
                "config must be an HMACWebhookAuthenticatorConfig"
            )
        self._config = config
        self._clock = clock

    def verify(
        self,
        *,
        timestamp: str | None,
        signature: str | None,
        body: bytes,
    ) -> None:
        """以常量时间比较签名，并拒绝过期或超前请求。"""
        if (
            timestamp is None
            or not timestamp.isascii()
            or not timestamp.isdigit()
            or len(timestamp) > 16
        ):
            raise WebhookAuthenticationRequired()
        if not isinstance(body, bytes) or not 1 <= len(body) <= (
            self._config.max_body_bytes
        ):
            raise WebhookAuthenticationRequired()

        match = (
            _SIGNATURE_PATTERN.fullmatch(signature)
            if signature is not None
            else None
        )
        if match is None:
            raise WebhookAuthenticationRequired()

        request_time = int(timestamp)
        now = int(self._clock())
        if abs(now - request_time) > self._config.tolerance_seconds:
            raise WebhookAuthenticationRequired()

        signed_payload = timestamp.encode("ascii") + b"." + body
        expected = hmac.new(
            self._config.secret.get_secret_value().encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, match.group(1)):
            raise WebhookAuthenticationRequired()
