import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field


AlertSeverity = Literal["INFO", "WARNING", "CRITICAL"]
Clock = Callable[[], float]


class AgentAlertPayload(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    service_name: str = Field(min_length=1, max_length=256)
    severity: AlertSeverity
    summary: str = Field(min_length=1, max_length=2048)
    starts_at: datetime
    fingerprint: str = Field(min_length=1, max_length=256)
    external_event_id: str = Field(min_length=1, max_length=256)


@dataclass(frozen=True)
class AgentAlertClientConfig:
    agent_url: str
    webhook_secret: str = ""
    timeout_seconds: float = 5.0
    max_response_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        parsed = urlparse(self.agent_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("agent_url must be a fixed HTTP(S) endpoint")
        if self.webhook_secret and (
            len(self.webhook_secret) < 32
            or self.webhook_secret != self.webhook_secret.strip()
            or _contains_ascii_control(self.webhook_secret)
        ):
            raise ValueError("webhook_secret is invalid")
        if not 0 < self.timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        if not 1 <= self.max_response_bytes <= 1024 * 1024:
            raise ValueError("max_response_bytes must be between 1 and 1048576")


@dataclass(frozen=True)
class AgentAlertDelivery:
    status_code: int
    response_data: dict[str, object]


class AgentAlertDeliveryError(RuntimeError):
    """Stable relay failure that does not expose upstream response content."""


class AgentAlertClient:
    def __init__(
        self,
        config: AgentAlertClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = time.time,
    ) -> None:
        self._config = config
        self._transport = transport
        self._clock = clock

    async def send(self, payload: AgentAlertPayload) -> AgentAlertDelivery:
        body = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._config.webhook_secret:
            headers.update(
                build_signed_headers(
                    body,
                    self._config.webhook_secret,
                    clock=self._clock,
                )
            )

        try:
            async with httpx.AsyncClient(
                timeout=self._config.timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST",
                    self._config.agent_url,
                    headers=headers,
                    content=body,
                ) as response:
                    response_body = await self._read_bounded_response(response)
        except httpx.TimeoutException as exc:
            raise AgentAlertDeliveryError("DevOps Agent alert request timed out") from exc
        except httpx.RequestError as exc:
            raise AgentAlertDeliveryError("DevOps Agent alert request failed") from exc

        if not 200 <= response.status_code < 300:
            raise AgentAlertDeliveryError(
                f"DevOps Agent rejected alert with HTTP {response.status_code}"
            )
        try:
            response_data = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentAlertDeliveryError("DevOps Agent returned invalid JSON") from exc
        if not isinstance(response_data, dict):
            raise AgentAlertDeliveryError("DevOps Agent returned an invalid response object")
        return AgentAlertDelivery(
            status_code=response.status_code,
            response_data=response_data,
        )

    async def _read_bounded_response(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._config.max_response_bytes:
                raise AgentAlertDeliveryError("DevOps Agent response is too large")
            chunks.append(chunk)
        return b"".join(chunks)


def build_signed_headers(
    body: bytes,
    secret: str,
    *,
    clock: Clock = time.time,
) -> dict[str, str]:
    timestamp = str(int(clock()))
    signed_payload = timestamp.encode("ascii") + b"." + body
    signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-DevOps-Agent-Timestamp": timestamp,
        "X-DevOps-Agent-Signature": f"sha256={signature}",
    }


def _contains_ascii_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
