import json

import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import (
    NotificationGatewayError,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.notifications import (
    NotificationWebhookConfig,
    RoutingNotificationGateway,
    WebhookNotificationGateway,
)
from devops_agent_platform.ports.notifications import NotificationMessage


def message() -> NotificationMessage:
    return NotificationMessage(
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        incident_id="inc_001",
        service_name="checkout",
        severity="CRITICAL",
        title="Checkout <@here> outage",
        summary="Payment dependency timed out.",
        recommendations=("Check payment health.",),
        idempotency_key="notification-key-001",
        trace_id="trc_notification_001",
    )


async def build_gateway(provider, handler):
    endpoint = (
        f"https://{provider}.example.test/webhook/secret"
        if provider != "pagerduty"
        else None
    )
    credential = SecretStr(endpoint or "pager-routing-key")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = WebhookNotificationGateway(
        NotificationWebhookConfig(
            provider=provider,
            credential=credential,
            endpoint_url=endpoint,
        ),
        http_client=client,
    )
    return gateway, client


@pytest.mark.parametrize("provider", ["slack", "teams", "pagerduty"])
async def test_provider_payloads_are_bounded_and_use_stable_identity(
    provider,
) -> None:
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        if provider == "slack":
            return httpx.Response(200, text="ok")
        if provider == "pagerduty":
            return httpx.Response(
                202,
                json={"status": "success", "dedup_key": "key"},
            )
        return httpx.Response(202)

    gateway, client = await build_gateway(provider, handler)
    try:
        result = await gateway.send(provider, message())
    finally:
        await client.aclose()

    assert result.provider == provider
    assert result.external_reference == "notification-key-001"
    body = captured["body"]
    if provider == "slack":
        serialized = json.dumps(body)
        assert "<@here>" not in serialized
        assert "&lt;@here&gt;" in serialized
    elif provider == "teams":
        assert body["attachments"][0]["content"]["type"] == "AdaptiveCard"
    else:
        assert captured["url"] == ("https://events.pagerduty.com/v2/enqueue")
        assert body["routing_key"] == "pager-routing-key"
        assert body["dedup_key"] == "notification-key-001"
        assert body["payload"]["severity"] == "critical"


async def test_provider_failure_is_sanitized_and_router_is_allowlisted() -> None:
    async def handler(request):
        del request
        return httpx.Response(
            500,
            text="token=provider-secret internal failure",
        )

    gateway, client = await build_gateway("slack", handler)
    router = RoutingNotificationGateway({"slack": gateway})
    try:
        with pytest.raises(
            NotificationGatewayError,
            match="rejected",
        ) as captured:
            await router.send("slack", message())
        assert "provider-secret" not in str(captured.value)
        with pytest.raises(AppValidationError, match="not configured"):
            await router.send("teams", message())
    finally:
        await router.close()
        await client.aclose()
