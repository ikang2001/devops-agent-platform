import asyncio
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import TicketingGatewayError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.ticketing import (
    TicketingGatewayObserverPort,
    TicketingGatewaySubmitOutcome,
    TicketingSubmitOutcome,
    TicketingSubmitRequest,
)
from devops_agent_platform.tools.sanitization import redact_sensitive_text

MonotonicClock = Callable[[], float]


@dataclass(frozen=True)
class HttpJsonTicketingGatewayConfig:
    """通用 HTTP JSON 工单网关的网络、安全和容量配置。"""

    endpoint_url: str
    bearer_token: SecretStr | None = field(default=None, repr=False)
    request_timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        """在创建连接池前拒绝危险地址、空凭据和无界配置。"""
        if not isinstance(self.endpoint_url, str):
            raise AppValidationError("endpoint_url must be a string")
        parsed = urlparse(self.endpoint_url)
        if (
            self.endpoint_url != self.endpoint_url.strip()
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.endpoint_url
            )
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise AppValidationError("endpoint_url must be an HTTP or HTTPS URL")
        _validate_positive_number(
            "request_timeout_seconds",
            self.request_timeout_seconds,
            maximum=60,
        )
        _validate_positive_integer(
            "max_response_bytes",
            self.max_response_bytes,
            maximum=1024 * 1024,
        )
        if self.bearer_token is not None:
            if not isinstance(self.bearer_token, SecretStr):
                raise AppValidationError("bearer_token must be a SecretStr or None")
            secret = self.bearer_token.get_secret_value()
            if (
                not 1 <= len(secret) <= 8192
                or secret != secret.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in secret
                )
            ):
                raise AppValidationError("bearer_token is invalid")


class HttpJsonTicketingGateway:
    """通过受控 HTTP JSON 协议提交外部工单。"""

    def __init__(
        self,
        config: HttpJsonTicketingGatewayConfig,
        http_client: httpx.AsyncClient | None = None,
        observer: TicketingGatewayObserverPort | None = None,
        monotonic_clock: MonotonicClock | None = None,
    ) -> None:
        """创建网关；外部传入的 AsyncClient 仍由调用方管理。"""
        if not isinstance(config, HttpJsonTicketingGatewayConfig):
            raise AppValidationError("config must be a HttpJsonTicketingGatewayConfig")
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
        self._observer = observer
        self._monotonic_clock = monotonic_clock or monotonic

    async def submit_ticket(
        self,
        request: TicketingSubmitRequest,
    ) -> TicketingSubmitOutcome:
        """提交工单并返回规范化结果；暂态异常交给上层重试。"""
        if not isinstance(request, TicketingSubmitRequest):
            raise AppValidationError("request must be a TicketingSubmitRequest")
        if self._closed:
            raise TicketingGatewayError("Ticketing gateway is closed")

        started_tick = self._read_monotonic()
        headers = self._build_headers(request)
        body = self._build_body(request)
        try:
            content = await self._post_bounded(headers, body)
            outcome = self._parse_response(content)
        except asyncio.CancelledError:
            self._observe(
                TicketingGatewaySubmitOutcome.CANCELLED,
                started_tick,
            )
            raise
        except TicketingGatewayError:
            self._observe(
                TicketingGatewaySubmitOutcome.GATEWAY_ERROR,
                started_tick,
            )
            raise

        self._observe(
            (
                TicketingGatewaySubmitOutcome.SUCCESS
                if outcome.succeeded
                else TicketingGatewaySubmitOutcome.BUSINESS_FAILURE
            ),
            started_tick,
        )
        return outcome

    async def close(self) -> None:
        """关闭内部连接池；重复调用安全。"""
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    def _build_headers(
        self,
        request: TicketingSubmitRequest,
    ) -> dict[str, str]:
        """构造只包含链路、幂等和认证信息的请求头。"""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": request.idempotency_key,
            "X-Trace-Id": request.trace_id,
        }
        if self._config.bearer_token is not None:
            headers["Authorization"] = (
                f"Bearer {self._config.bearer_token.get_secret_value()}"
            )
        return headers

    @staticmethod
    def _build_body(
        request: TicketingSubmitRequest,
    ) -> dict[str, Any]:
        """构造提交给工单中间层的最小业务载荷。"""
        return {
            "tenant_id": request.tenant_id,
            "ticket_submission_id": request.ticket_submission_id,
            "ticket_draft_id": request.ticket_draft_id,
            "target_system": request.target_system,
            "title": request.title,
            "description": request.description,
            "priority": request.priority,
            "evidence_ids": list(request.evidence_ids),
            "recommendations": list(request.recommendations),
        }

    async def _post_bounded(
        self,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> bytes:
        """流式读取有限响应，并隐藏状态正文、请求头和令牌。"""
        content = bytearray()
        try:
            async with self._http_client.stream(
                "POST",
                self._config.endpoint_url,
                headers=headers,
                json=body,
                timeout=self._config.request_timeout_seconds,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._config.max_response_bytes:
                        raise TicketingGatewayError(
                            "Ticketing response exceeds size limit"
                        )
        except asyncio.CancelledError:
            raise
        except TicketingGatewayError:
            raise
        except httpx.HTTPError:
            raise TicketingGatewayError("Ticketing gateway request failed") from None
        if not content:
            raise TicketingGatewayError("Ticketing gateway returned an empty response")
        return bytes(content)

    @staticmethod
    def _parse_response(content: bytes) -> TicketingSubmitOutcome:
        """把外部响应严格转换为应用层规范结果。"""
        try:
            document = json.loads(
                content,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise TicketingGatewayError(
                "Ticketing gateway returned invalid JSON"
            ) from None
        if not isinstance(document, dict):
            raise TicketingGatewayError("Ticketing gateway response must be an object")
        allowed_keys = {
            "succeeded",
            "external_ticket_id",
            "external_ticket_url",
            "failure_reason",
        }
        if set(document) - allowed_keys:
            raise TicketingGatewayError(
                "Ticketing gateway response contains unsupported fields"
            )
        if not isinstance(document.get("succeeded"), bool):
            raise TicketingGatewayError(
                "Ticketing gateway response succeeded field is invalid"
            )
        if document["succeeded"] and document.get("failure_reason") is not None:
            raise TicketingGatewayError("Ticketing gateway success response is invalid")
        if not document["succeeded"] and (
            document.get("external_ticket_id") is not None
            or document.get("external_ticket_url") is not None
        ):
            raise TicketingGatewayError("Ticketing gateway failure response is invalid")
        try:
            if document["succeeded"]:
                return TicketingSubmitOutcome(
                    succeeded=True,
                    external_ticket_id=document.get("external_ticket_id"),
                    external_ticket_url=document.get("external_ticket_url"),
                )
            return TicketingSubmitOutcome(
                succeeded=False,
                failure_reason=_safe_failure_reason(document.get("failure_reason")),
            )
        except AppValidationError as exc:
            raise TicketingGatewayError(
                "Ticketing gateway response outcome is invalid"
            ) from exc

    def _read_monotonic(self) -> float:
        """读取有限单调时钟，避免测试或错误注入污染耗时指标。"""
        value = self._monotonic_clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise AppValidationError("monotonic clock must return a finite number")
        return float(value)

    def _observe(
        self,
        outcome: TicketingGatewaySubmitOutcome,
        started_tick: float,
    ) -> None:
        """尽力上报固定结果；监控故障不能改变工单提交结果。"""
        if self._observer is None:
            return
        try:
            duration = max(0.0, self._read_monotonic() - started_tick)
            self._observer.observe_ticketing_gateway_submit(
                outcome,
                duration,
            )
        except Exception:
            return


def _validate_positive_number(
    field_name: str,
    value: float,
    *,
    maximum: float,
) -> None:
    """校验有限正数超时配置。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 < float(value) <= maximum
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _safe_failure_reason(value: object) -> object:
    """清洗供应商业务失败原因；非字符串仍交给领域契约拒绝。"""
    if not isinstance(value, str):
        return value
    reason = "".join(
        " " if ord(character) < 32 or ord(character) == 127 else character
        for character in value
    )
    reason = " ".join(reason.split()).strip()
    if not reason:
        return reason
    return redact_sensitive_text(reason)[0][:2048]


def _validate_positive_integer(
    field_name: str,
    value: int,
    *,
    maximum: int,
) -> None:
    """校验正整数容量配置。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _reject_json_constant(value: str) -> None:
    """拒绝 JSON 标准之外的 NaN 和 Infinity。"""
    raise ValueError(f"invalid JSON constant: {value}")
