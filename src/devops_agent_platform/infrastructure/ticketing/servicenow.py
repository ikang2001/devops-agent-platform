import asyncio
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

import httpx
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import TicketingGatewayError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.ticketing._vendor_http import (
    MonotonicClock,
    VendorTicketingHttpSupport,
    classify_http_failure,
    validate_bounded_text,
)
from devops_agent_platform.ports.ticketing import (
    TicketingGatewayObserverPort,
    TicketingGatewaySubmitOutcome,
    TicketingSubmitOutcome,
    TicketingSubmitRequest,
)


@dataclass(frozen=True)
class ServiceNowTicketingGatewayConfig:
    """ServiceNow Table API 建单配置。"""

    base_url: str
    username: str
    password: SecretStr = field(repr=False)
    table: str = "incident"
    request_timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if (
            self.base_url != self.base_url.strip()
            or self.base_url.endswith("/")
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise AppValidationError("ServiceNow base_url is invalid")
        validate_bounded_text(
            "ServiceNow username",
            self.username,
            256,
            allow_at_sign=True,
        )
        validate_bounded_text("ServiceNow table", self.table, 128)
        if not isinstance(self.password, SecretStr):
            raise AppValidationError("ServiceNow password must be a SecretStr")
        password = self.password.get_secret_value()
        if (
            not 1 <= len(password) <= 8192
            or password != password.strip()
            or any(
                ord(character) < 32 or ord(character) == 127 for character in password
            )
        ):
            raise AppValidationError("ServiceNow password is invalid")


class ServiceNowTicketingGateway:
    """直接调用 ServiceNow Table API 创建 Incident。"""

    def __init__(
        self,
        config: ServiceNowTicketingGatewayConfig,
        http_client: httpx.AsyncClient | None = None,
        observer: TicketingGatewayObserverPort | None = None,
        monotonic_clock: MonotonicClock | None = None,
    ) -> None:
        if not isinstance(config, ServiceNowTicketingGatewayConfig):
            raise AppValidationError(
                "config must be a ServiceNowTicketingGatewayConfig"
            )
        self._config = config
        self._support = VendorTicketingHttpSupport(
            request_timeout_seconds=config.request_timeout_seconds,
            max_response_bytes=config.max_response_bytes,
            http_client=http_client,
            observer=observer,
            monotonic_clock=monotonic_clock,
        )

    async def submit_ticket(
        self,
        request: TicketingSubmitRequest,
    ) -> TicketingSubmitOutcome:
        if not isinstance(request, TicketingSubmitRequest):
            raise AppValidationError("request must be a TicketingSubmitRequest")
        started_tick = self._support.read_monotonic()
        try:
            status_code, content = await self._support.post_json(
                endpoint=(
                    f"{self._config.base_url}/api/now/table/"
                    f"{quote(self._config.table, safe='')}"
                ),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Idempotency-Key": request.idempotency_key,
                    "X-Trace-Id": request.trace_id,
                },
                body=self._build_body(request),
                auth=httpx.BasicAuth(
                    self._config.username,
                    self._config.password.get_secret_value(),
                ),
            )
            failure = classify_http_failure(status_code, "ServiceNow")
            if failure is not None:
                outcome = TicketingSubmitOutcome(
                    succeeded=False,
                    failure_reason=failure,
                )
            else:
                outcome = self._parse_success(content)
        except asyncio.CancelledError:
            self._support.observe(
                TicketingGatewaySubmitOutcome.CANCELLED,
                started_tick,
            )
            raise
        except TicketingGatewayError:
            self._support.observe(
                TicketingGatewaySubmitOutcome.GATEWAY_ERROR,
                started_tick,
            )
            raise
        self._support.observe(
            (
                TicketingGatewaySubmitOutcome.SUCCESS
                if outcome.succeeded
                else TicketingGatewaySubmitOutcome.BUSINESS_FAILURE
            ),
            started_tick,
        )
        return outcome

    async def close(self) -> None:
        await self._support.close()

    @staticmethod
    def _build_body(request: TicketingSubmitRequest) -> dict[str, str]:
        priority_map = {
            "P1": ("1", "1"),
            "P2": ("2", "2"),
            "P3": ("3", "3"),
        }
        impact, urgency = priority_map.get(
            request.priority,
            ("3", "3"),
        )
        recommendations = "\n".join(f"- {item}" for item in request.recommendations)
        return {
            "short_description": request.title,
            "description": (
                f"{request.description}\n\n"
                f"Evidence IDs: {', '.join(request.evidence_ids) or 'none'}\n"
                f"Recommendations:\n{recommendations or '- none'}"
            ),
            "impact": impact,
            "urgency": urgency,
            "correlation_id": request.idempotency_key,
            "correlation_display": request.ticket_submission_id,
        }

    def _parse_success(self, content: bytes) -> TicketingSubmitOutcome:
        document = self._support.parse_object(content, "ServiceNow")
        result = document.get("result")
        if not isinstance(result, dict):
            raise TicketingGatewayError("ServiceNow response result is invalid")
        sys_id = result.get("sys_id")
        number = result.get("number")
        if not _valid_identifier(sys_id, 64) or not _valid_identifier(
            number,
            256,
        ):
            raise TicketingGatewayError(
                "ServiceNow response ticket identity is invalid"
            )
        record_path = quote(
            f"{self._config.table}.do?sys_id={sys_id}",
            safe="",
        )
        return TicketingSubmitOutcome(
            succeeded=True,
            external_ticket_id=number,
            external_ticket_url=(
                f"{self._config.base_url}/nav_to.do?uri={record_path}"
            ),
        )


def _valid_identifier(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and value == value.strip()
        and not any(character.isspace() for character in value)
        and all(ord(character) >= 32 and ord(character) != 127 for character in value)
    )
