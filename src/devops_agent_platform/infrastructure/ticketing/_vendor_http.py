import asyncio
import json
import math
from collections.abc import Callable
from time import monotonic
from typing import Any

import httpx

from devops_agent_platform.application.exceptions import TicketingGatewayError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.ticketing import (
    TicketingGatewayObserverPort,
    TicketingGatewaySubmitOutcome,
)

MonotonicClock = Callable[[], float]


class VendorTicketingHttpSupport:
    """供应商工单适配器共享的有界 HTTP 和指标能力。"""

    def __init__(
        self,
        *,
        request_timeout_seconds: float,
        max_response_bytes: int,
        http_client: httpx.AsyncClient | None,
        observer: TicketingGatewayObserverPort | None,
        monotonic_clock: MonotonicClock | None,
    ) -> None:
        self._validate_limits(
            request_timeout_seconds,
            max_response_bytes,
        )
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self.owns_http_client = http_client is None
        self.http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.request_timeout_seconds),
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
            trust_env=False,
        )
        self.observer = observer
        self.monotonic_clock = monotonic_clock or monotonic
        self.closed = False

    async def post_json(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        body: dict[str, Any],
        auth: httpx.Auth | None = None,
    ) -> tuple[int, bytes]:
        """提交一次不跟随重定向的请求，并限制响应体大小。"""
        if self.closed:
            raise TicketingGatewayError("Ticketing gateway is closed")
        content = bytearray()
        try:
            async with self.http_client.stream(
                "POST",
                endpoint,
                headers=headers,
                json=body,
                auth=auth,
                timeout=self.request_timeout_seconds,
                follow_redirects=False,
            ) as response:
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise TicketingGatewayError(
                            "Ticketing response exceeds size limit"
                        )
                return response.status_code, bytes(content)
        except asyncio.CancelledError:
            raise
        except TicketingGatewayError:
            raise
        except httpx.HTTPError:
            raise TicketingGatewayError("Ticketing gateway request failed") from None

    @staticmethod
    def parse_object(content: bytes, provider: str) -> dict[str, Any]:
        """严格解析 JSON 对象，错误中不携带供应商响应正文。"""
        try:
            document = json.loads(
                content,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise TicketingGatewayError(f"{provider} returned invalid JSON") from None
        if not isinstance(document, dict):
            raise TicketingGatewayError(f"{provider} response must be an object")
        return document

    async def close(self) -> None:
        """关闭内部连接池；调用方注入的连接池仍由调用方管理。"""
        if self.closed:
            return
        self.closed = True
        if self.owns_http_client:
            await self.http_client.aclose()

    def read_monotonic(self) -> float:
        value = self.monotonic_clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise AppValidationError("monotonic clock must return a finite number")
        return float(value)

    def observe(
        self,
        outcome: TicketingGatewaySubmitOutcome,
        started_tick: float,
    ) -> None:
        """指标为非关键路径，观察器失败不能改变外部提交结果。"""
        if self.observer is None:
            return
        try:
            duration = max(0.0, self.read_monotonic() - started_tick)
            self.observer.observe_ticketing_gateway_submit(
                outcome,
                duration,
            )
        except Exception:
            return

    @staticmethod
    def _validate_limits(
        request_timeout_seconds: float,
        max_response_bytes: int,
    ) -> None:
        if (
            isinstance(request_timeout_seconds, bool)
            or not isinstance(request_timeout_seconds, int | float)
            or not 0 < float(request_timeout_seconds) <= 60
        ):
            raise AppValidationError("request_timeout_seconds is invalid")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 1 <= max_response_bytes <= 1024 * 1024
        ):
            raise AppValidationError("max_response_bytes is invalid")


def classify_http_failure(
    status_code: int,
    provider: str,
) -> str | None:
    """把确定性 4xx 转成业务失败；限流和服务故障留给上层重试。"""
    if 200 <= status_code < 300:
        return None
    if status_code == 429 or status_code >= 500:
        raise TicketingGatewayError(f"{provider} is temporarily unavailable")
    return f"{provider} rejected ticket creation"


def validate_bounded_text(
    field_name: str,
    value: str,
    maximum: int,
    *,
    allow_at_sign: bool = False,
) -> str:
    """校验将进入供应商 URL、认证或字段映射的单行配置。"""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(character.isspace() for character in value)
    ):
        raise AppValidationError(f"{field_name} is invalid")
    allowed = "._:-@+" if allow_at_sign else "._:-"
    if any(not (character.isalnum() or character in allowed) for character in value):
        raise AppValidationError(f"{field_name} is invalid")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")
