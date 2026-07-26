import asyncio
import json
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import (
    NotificationGatewayError,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.notifications import (
    NotificationDeliveryOutcome,
    NotificationGatewayPort,
    NotificationMessage,
)

_PROVIDERS = frozenset({"slack", "teams", "pagerduty"})
_PAGERDUTY_ENDPOINT = "https://events.pagerduty.com/v2/enqueue"


@dataclass(frozen=True)
class NotificationWebhookConfig:
    """单一通知供应商的凭据和有界网络配置。"""

    provider: str
    credential: SecretStr = field(repr=False)
    endpoint_url: str | None = None
    request_timeout_seconds: float = 5.0
    max_response_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if self.provider not in _PROVIDERS:
            raise AppValidationError("notification provider is invalid")
        if not isinstance(self.credential, SecretStr):
            raise AppValidationError("notification credential must be a SecretStr")
        secret = self.credential.get_secret_value()
        if (
            not 1 <= len(secret) <= 8192
            or secret != secret.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in secret)
        ):
            raise AppValidationError("notification credential is invalid")
        if self.provider == "pagerduty":
            if self.endpoint_url is not None:
                raise AppValidationError("PagerDuty endpoint cannot be overridden")
        else:
            _validate_endpoint(self.endpoint_url)
            if self.endpoint_url != secret:
                raise AppValidationError("webhook credential and endpoint must match")
        if (
            isinstance(self.request_timeout_seconds, bool)
            or not isinstance(self.request_timeout_seconds, int | float)
            or not 0 < float(self.request_timeout_seconds) <= 60
        ):
            raise AppValidationError("notification request timeout is invalid")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 1 <= self.max_response_bytes <= 1024 * 1024
        ):
            raise AppValidationError("notification response limit is invalid")


class WebhookNotificationGateway:
    """向 Slack、Teams 或 PagerDuty 投递受控 RCA 摘要。"""

    def __init__(
        self,
        config: NotificationWebhookConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout_seconds),
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
            trust_env=False,
        )
        self._closed = False

    async def send(
        self,
        target_system: str,
        message: NotificationMessage,
    ) -> NotificationDeliveryOutcome:
        if target_system != self._config.provider:
            raise AppValidationError("notification target does not match gateway")
        if self._closed:
            raise NotificationGatewayError("Notification gateway is closed")
        endpoint, body = self._build_request(message)
        content = await self._post_bounded(endpoint, body)
        self._validate_response(content)
        return NotificationDeliveryOutcome(
            provider=self._config.provider,
            external_reference=message.idempotency_key,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    def _build_request(
        self,
        message: NotificationMessage,
    ) -> tuple[str, dict]:
        provider = self._config.provider
        if provider == "slack":
            return self._config.endpoint_url or "", _slack_body(message)
        if provider == "teams":
            return self._config.endpoint_url or "", _teams_body(message)
        return _PAGERDUTY_ENDPOINT, _pagerduty_body(
            message,
            self._config.credential.get_secret_value(),
        )

    async def _post_bounded(
        self,
        endpoint: str,
        body: dict,
    ) -> bytes:
        content = bytearray()
        try:
            async with self._http_client.stream(
                "POST",
                endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Trace-Id": body.get("trace_id", "notification"),
                },
                json=body,
                timeout=self._config.request_timeout_seconds,
            ) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise NotificationGatewayError(
                        "Notification provider rejected the request"
                    )
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._config.max_response_bytes:
                        raise NotificationGatewayError(
                            "Notification response exceeds size limit"
                        )
        except asyncio.CancelledError:
            raise
        except NotificationGatewayError:
            raise
        except httpx.HTTPError:
            raise NotificationGatewayError(
                "Notification provider request failed"
            ) from None
        return bytes(content)

    def _validate_response(self, content: bytes) -> None:
        if self._config.provider == "slack":
            if content and content.strip().lower() != b"ok":
                raise NotificationGatewayError("Slack returned an invalid response")
            return
        if self._config.provider != "pagerduty":
            return
        try:
            document = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise NotificationGatewayError("PagerDuty returned invalid JSON") from None
        if not isinstance(document, dict) or document.get("status") != "success":
            raise NotificationGatewayError("PagerDuty returned an invalid response")


class RoutingNotificationGateway:
    """只允许路由到显式配置的通知供应商。"""

    def __init__(
        self,
        routes: dict[str, NotificationGatewayPort],
    ) -> None:
        if not routes or set(routes) - _PROVIDERS:
            raise AppValidationError("notification routes are invalid")
        self._routes = dict(routes)
        self._closed = False

    async def send(
        self,
        target_system: str,
        message: NotificationMessage,
    ) -> NotificationDeliveryOutcome:
        if self._closed:
            raise NotificationGatewayError("Notification router is closed")
        gateway = self._routes.get(target_system)
        if gateway is None:
            raise AppValidationError("notification target is not configured")
        return await gateway.send(target_system, message)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        for gateway in self._routes.values():
            close = getattr(gateway, "close", None)
            if close is None:
                continue
            try:
                await close()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup(
                "Failed to close notification gateways",
                errors,
            )


def _slack_body(message: NotificationMessage) -> dict:
    headline = _slack_escape(f"[{message.severity}] {message.title}")
    detail = (
        f"*Service:* {_slack_escape(message.service_name)}\n"
        f"*Incident:* `{_slack_escape(message.incident_id)}`\n"
        f"*RCA:* {_slack_escape(message.summary)}"
    )
    return {
        "text": headline,
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": headline[:150]},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": detail[:2900]},
            },
        ],
    }


def _slack_escape(value: str) -> str:
    """阻止报告文本被解释成提及、链接或额外 Slack 标记。"""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(
            ">",
            "&gt;",
        )
    )


def _teams_body(message: NotificationMessage) -> dict:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": ("http://adaptivecards.io/schemas/adaptive-card.json"),
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "weight": "Bolder",
                            "text": f"[{message.severity}] {message.title}",
                            "wrap": True,
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {
                                    "title": "Service",
                                    "value": message.service_name,
                                },
                                {
                                    "title": "Incident",
                                    "value": message.incident_id,
                                },
                            ],
                        },
                        {
                            "type": "TextBlock",
                            "text": message.summary,
                            "wrap": True,
                        },
                    ],
                },
            }
        ],
    }


def _pagerduty_body(
    message: NotificationMessage,
    routing_key: str,
) -> dict:
    severity = {
        "CRITICAL": "critical",
        "WARNING": "warning",
        "INFO": "info",
    }.get(message.severity, "error")
    return {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": message.idempotency_key,
        "payload": {
            "summary": message.title,
            "source": message.service_name,
            "severity": severity,
            "component": message.incident_id,
            "custom_details": {
                "workflow_run_id": message.workflow_run_id,
                "rca_summary": message.summary,
                "trace_id": message.trace_id,
            },
        },
    }


def _validate_endpoint(value: str | None) -> None:
    if not isinstance(value, str):
        raise AppValidationError("notification webhook endpoint is required")
    parsed = urlparse(value)
    if (
        value != value.strip()
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise AppValidationError("notification webhook endpoint is invalid")
