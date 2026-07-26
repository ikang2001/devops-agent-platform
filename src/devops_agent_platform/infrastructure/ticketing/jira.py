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
class JiraTicketingGatewayConfig:
    """Jira Cloud 建单所需的固定站点、项目和凭据。"""

    base_url: str
    user_email: str
    api_token: SecretStr = field(repr=False)
    project_key: str
    issue_type: str = "Task"
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
            raise AppValidationError("Jira base_url is invalid")
        validate_bounded_text(
            "Jira user_email",
            self.user_email,
            320,
            allow_at_sign=True,
        )
        validate_bounded_text("Jira project_key", self.project_key, 64)
        if (
            not isinstance(self.issue_type, str)
            or not 1 <= len(self.issue_type) <= 128
            or self.issue_type != self.issue_type.strip()
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.issue_type
            )
        ):
            raise AppValidationError("Jira issue_type is invalid")
        if not isinstance(self.api_token, SecretStr):
            raise AppValidationError("Jira api_token must be a SecretStr")
        token = self.api_token.get_secret_value()
        if (
            not 1 <= len(token) <= 8192
            or token != token.strip()
            or any(character.isspace() for character in token)
            or any(ord(character) < 32 or ord(character) == 127 for character in token)
        ):
            raise AppValidationError("Jira api_token is invalid")


class JiraTicketingGateway:
    """直接调用 Jira Cloud REST API v3 创建 Issue。"""

    def __init__(
        self,
        config: JiraTicketingGatewayConfig,
        http_client: httpx.AsyncClient | None = None,
        observer: TicketingGatewayObserverPort | None = None,
        monotonic_clock: MonotonicClock | None = None,
    ) -> None:
        if not isinstance(config, JiraTicketingGatewayConfig):
            raise AppValidationError("config must be a JiraTicketingGatewayConfig")
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
                endpoint=f"{self._config.base_url}/rest/api/3/issue",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Idempotency-Key": request.idempotency_key,
                    "X-Trace-Id": request.trace_id,
                },
                body=self._build_body(request),
                auth=httpx.BasicAuth(
                    self._config.user_email,
                    self._config.api_token.get_secret_value(),
                ),
            )
            failure = classify_http_failure(status_code, "Jira")
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

    def _build_body(self, request: TicketingSubmitRequest) -> dict:
        evidence = ", ".join(request.evidence_ids) or "none"
        recommendations = "\n".join(f"- {item}" for item in request.recommendations)
        description = (
            f"Priority: {request.priority}\n"
            f"Tenant: {request.tenant_id}\n"
            f"Evidence IDs: {evidence}\n\n"
            f"{request.description}\n\n"
            f"Recommendations:\n{recommendations or '- none'}"
        )
        return {
            "fields": {
                "project": {"key": self._config.project_key},
                "issuetype": {"name": self._config.issue_type},
                "summary": request.title,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": description,
                                }
                            ],
                        }
                    ],
                },
            },
            "properties": [
                {
                    "key": "devops.agent.submission",
                    "value": {
                        "ticket_submission_id": request.ticket_submission_id,
                        "idempotency_key": request.idempotency_key,
                        "trace_id": request.trace_id,
                    },
                }
            ],
        }

    def _parse_success(self, content: bytes) -> TicketingSubmitOutcome:
        document = self._support.parse_object(content, "Jira")
        issue_id = document.get("key")
        if (
            not isinstance(issue_id, str)
            or not 1 <= len(issue_id) <= 256
            or issue_id != issue_id.strip()
            or any(character.isspace() for character in issue_id)
            or any(
                ord(character) < 32 or ord(character) == 127 for character in issue_id
            )
        ):
            raise TicketingGatewayError("Jira response issue key is invalid")
        return TicketingSubmitOutcome(
            succeeded=True,
            external_ticket_id=issue_id,
            external_ticket_url=(
                f"{self._config.base_url}/browse/{quote(issue_id, safe='')}"
            ),
        )
