import asyncio
import base64
import json
from dataclasses import replace

import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import TicketingGatewayError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.ticketing import (
    JiraTicketingGateway,
    JiraTicketingGatewayConfig,
    RoutingTicketingGateway,
    ServiceNowTicketingGateway,
    ServiceNowTicketingGatewayConfig,
)
from devops_agent_platform.ports.ticketing import (
    TicketingSubmitOutcome,
    TicketingSubmitRequest,
)


def build_request(
    target_system: str = "jira",
) -> TicketingSubmitRequest:
    return TicketingSubmitRequest(
        tenant_id="tenant_001",
        ticket_submission_id="tsb_001",
        ticket_draft_id="tdf_001",
        target_system=target_system,
        title="Checkout latency root cause",
        description="Payment dependency exceeded its latency budget.",
        priority="P1",
        evidence_ids=("evd_metric", "evd_trace"),
        recommendations=("Rollback payment release.",),
        idempotency_key="ticket-submit-tsb_001-v1",
        trace_id="trc_ticket_submit_001",
    )


async def test_jira_gateway_maps_request_and_parses_issue() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            201,
            json={"id": "10001", "key": "OPS-42", "self": "hidden"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = JiraTicketingGateway(
            JiraTicketingGatewayConfig(
                base_url="https://example.atlassian.net",
                user_email="ops@example.com",
                api_token=SecretStr("jira-secret"),
                project_key="OPS",
                issue_type="Service Request",
            ),
            http_client=client,
        )
        result = await gateway.submit_ticket(build_request())

    assert result == TicketingSubmitOutcome(
        succeeded=True,
        external_ticket_id="OPS-42",
        external_ticket_url="https://example.atlassian.net/browse/OPS-42",
    )
    request = captured[0]
    assert request.url.path == "/rest/api/3/issue"
    assert request.headers["Idempotency-Key"] == "ticket-submit-tsb_001-v1"
    encoded_auth = request.headers["Authorization"].removeprefix("Basic ")
    assert base64.b64decode(encoded_auth).decode() == ("ops@example.com:jira-secret")
    body = json.loads(request.content)
    assert body["fields"]["project"] == {"key": "OPS"}
    assert body["fields"]["issuetype"] == {"name": "Service Request"}
    assert body["fields"]["summary"] == "Checkout latency root cause"
    description = body["fields"]["description"]["content"][0]["content"][0]["text"]
    assert "evd_metric, evd_trace" in description
    assert "Rollback payment release." in description
    assert body["properties"][0]["value"]["ticket_submission_id"] == "tsb_001"


@pytest.mark.parametrize(
    ("status_code", "raises"),
    [
        (400, False),
        (401, False),
        (429, True),
        (503, True),
    ],
)
async def test_jira_gateway_classifies_provider_failures(
    status_code: int,
    raises: bool,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "secret"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = JiraTicketingGateway(
            JiraTicketingGatewayConfig(
                base_url="https://example.atlassian.net",
                user_email="ops@example.com",
                api_token=SecretStr("jira-secret"),
                project_key="OPS",
            ),
            http_client=client,
        )
        if raises:
            with pytest.raises(TicketingGatewayError):
                await gateway.submit_ticket(build_request())
        else:
            result = await gateway.submit_ticket(build_request())
            assert result.succeeded is False
            assert result.failure_reason == "Jira rejected ticket creation"
            assert "secret" not in result.failure_reason


async def test_servicenow_gateway_maps_priority_and_parses_incident() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            201,
            json={
                "result": {
                    "sys_id": "a" * 32,
                    "number": "INC0012345",
                }
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = ServiceNowTicketingGateway(
            ServiceNowTicketingGatewayConfig(
                base_url="https://example.service-now.com",
                username="devops.integration",
                password=SecretStr("snow-secret"),
            ),
            http_client=client,
        )
        result = await gateway.submit_ticket(build_request("servicenow"))

    assert result.succeeded is True
    assert result.external_ticket_id == "INC0012345"
    assert result.external_ticket_url == (
        "https://example.service-now.com/nav_to.do?"
        "uri=incident.do%3Fsys_id%3D"
        f"{'a' * 32}"
    )
    request = captured[0]
    assert request.url.path == "/api/now/table/incident"
    body = json.loads(request.content)
    assert body["impact"] == "1"
    assert body["urgency"] == "1"
    assert body["correlation_id"] == "ticket-submit-tsb_001-v1"
    assert body["correlation_display"] == "tsb_001"


async def test_servicenow_gateway_rejects_malformed_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"result": {"sys_id": "a" * 32}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = ServiceNowTicketingGateway(
            ServiceNowTicketingGatewayConfig(
                base_url="https://example.service-now.com",
                username="devops.integration",
                password=SecretStr("snow-secret"),
            ),
            http_client=client,
        )
        with pytest.raises(TicketingGatewayError):
            await gateway.submit_ticket(build_request("servicenow"))


class FakeGateway:
    def __init__(self, ticket_id: str) -> None:
        self.ticket_id = ticket_id
        self.calls: list[TicketingSubmitRequest] = []
        self.close_calls = 0

    async def submit_ticket(
        self,
        request: TicketingSubmitRequest,
    ) -> TicketingSubmitOutcome:
        self.calls.append(request)
        return TicketingSubmitOutcome(
            succeeded=True,
            external_ticket_id=self.ticket_id,
        )

    async def close(self) -> None:
        self.close_calls += 1


async def test_routing_gateway_selects_exact_provider_and_fallback() -> None:
    jira = FakeGateway("OPS-42")
    fallback = FakeGateway("GEN-1")
    router = RoutingTicketingGateway(
        {"jira": jira},
        fallback=fallback,
    )

    jira_result = await router.submit_ticket(build_request())
    generic_result = await router.submit_ticket(
        replace(build_request(), target_system="custom")
    )
    await router.close()
    await router.close()

    assert jira_result.external_ticket_id == "OPS-42"
    assert generic_result.external_ticket_id == "GEN-1"
    assert len(jira.calls) == 1
    assert len(fallback.calls) == 1
    assert jira.close_calls == 1
    assert fallback.close_calls == 1


async def test_routing_gateway_returns_stable_unsupported_failure() -> None:
    router = RoutingTicketingGateway({"jira": FakeGateway("OPS-42")})

    result = await router.submit_ticket(
        replace(build_request(), target_system="unknown")
    )

    assert result == TicketingSubmitOutcome(
        succeeded=False,
        failure_reason="Unsupported ticket target system",
    )


def test_vendor_configs_reject_base_paths_and_invalid_routes() -> None:
    with pytest.raises(AppValidationError, match="Jira base_url"):
        JiraTicketingGatewayConfig(
            base_url="https://example.atlassian.net/rest/api",
            user_email="ops@example.com",
            api_token=SecretStr("jira-secret"),
            project_key="OPS",
        )
    with pytest.raises(AppValidationError, match="ServiceNow base_url"):
        ServiceNowTicketingGatewayConfig(
            base_url="https://example.service-now.com/custom",
            username="devops.integration",
            password=SecretStr("snow-secret"),
        )
    with pytest.raises(AppValidationError, match="route target"):
        RoutingTicketingGateway({"jira\nadmin": FakeGateway("OPS-42")})


async def test_provider_identifiers_reject_whitespace_and_controls() -> None:
    async def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"key": "OPS-\n42"}, request=request)

    async def snow_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "result": {
                    "sys_id": "a" * 32,
                    "number": "INC 0012345",
                }
            },
            request=request,
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(jira_handler)) as jira_client,
        httpx.AsyncClient(transport=httpx.MockTransport(snow_handler)) as snow_client,
    ):
        jira = JiraTicketingGateway(
            JiraTicketingGatewayConfig(
                base_url="https://example.atlassian.net",
                user_email="ops@example.com",
                api_token=SecretStr("jira-secret"),
                project_key="OPS",
            ),
            http_client=jira_client,
        )
        snow = ServiceNowTicketingGateway(
            ServiceNowTicketingGatewayConfig(
                base_url="https://example.service-now.com",
                username="devops.integration",
                password=SecretStr("snow-secret"),
            ),
            http_client=snow_client,
        )
        with pytest.raises(TicketingGatewayError, match="issue key"):
            await jira.submit_ticket(build_request())
        with pytest.raises(TicketingGatewayError, match="ticket identity"):
            await snow.submit_ticket(build_request("servicenow"))


async def test_routing_gateway_validates_request_and_propagates_cancellation() -> None:
    class CancellingGateway(FakeGateway):
        async def close(self) -> None:
            raise asyncio.CancelledError

    router = RoutingTicketingGateway({"jira": CancellingGateway("OPS-42")})
    with pytest.raises(AppValidationError, match="TicketingSubmitRequest"):
        await router.submit_ticket(object())  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await router.close()
