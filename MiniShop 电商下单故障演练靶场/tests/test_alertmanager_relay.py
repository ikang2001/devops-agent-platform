import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from app.agent_alerts import (
    AgentAlertClient,
    AgentAlertClientConfig,
    AgentAlertDelivery,
    AgentAlertPayload,
)
from app.alertmanager import (
    AlertmanagerAlert,
    AlertmanagerAlertMapper,
    AlertmanagerRelay,
    AlertmanagerRelayResult,
    AlertmanagerWebhook,
)
from app.main import app


def build_native_alert(
    *,
    status: str = "firing",
    starts_at: str = "2026-07-18T10:00:00Z",
) -> AlertmanagerAlert:
    return AlertmanagerAlert.model_validate(
        {
            "status": status,
            "labels": {
                "alertname": "MiniShopInventoryDbTimeout",
                "severity": "P1",
                "service_name": "inventory-service",
            },
            "annotations": {
                "summary": "inventory database timeout detected",
            },
            "startsAt": starts_at,
            "fingerprint": "abcdef0123456789",
        }
    )


def test_mapper_normalizes_native_alertmanager_contract() -> None:
    mapper = AlertmanagerAlertMapper(default_tenant_id="demo")

    payload = mapper.map(build_native_alert())

    assert payload is not None
    assert payload.tenant_id == "demo"
    assert payload.service_name == "inventory-service"
    assert payload.severity == "CRITICAL"
    assert payload.source == "alertmanager"
    assert payload.external_event_id.startswith("am_")


def test_external_event_id_is_retry_stable_but_recurrence_safe() -> None:
    mapper = AlertmanagerAlertMapper(default_tenant_id="demo")

    first = mapper.map(build_native_alert())
    retry = mapper.map(build_native_alert())
    recurrence = mapper.map(build_native_alert(starts_at="2026-07-18T11:00:00Z"))

    assert first is not None and retry is not None and recurrence is not None
    assert first.external_event_id == retry.external_event_id
    assert first.external_event_id != recurrence.external_event_id


def test_mapper_ignores_resolved_alert() -> None:
    mapper = AlertmanagerAlertMapper(default_tenant_id="demo")

    assert mapper.map(build_native_alert(status="resolved")) is None


def test_agent_client_signs_exact_forwarded_body() -> None:
    captured: dict[str, object] = {}
    secret = "minishop-demo-webhook-secret-32-bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        captured["body"] = body
        captured["timestamp"] = request.headers["X-DevOps-Agent-Timestamp"]
        captured["signature"] = request.headers["X-DevOps-Agent-Signature"]
        return httpx.Response(
            200,
            json={"data": {"incident_id": "inc_001"}},
        )

    client = AgentAlertClient(
        AgentAlertClientConfig(
            agent_url="http://agent:8000/api/v1/alerts",
            webhook_secret=secret,
        ),
        transport=httpx.MockTransport(handler),
        clock=lambda: 1_752_832_800,
    )
    payload = AgentAlertPayload(
        tenant_id="demo",
        source="alertmanager",
        service_name="inventory-service",
        severity="CRITICAL",
        summary="inventory timeout",
        starts_at=datetime(2026, 7, 18, 10, tzinfo=timezone.utc),
        fingerprint="abcdef0123456789",
        external_event_id="am_test",
    )

    delivery = asyncio.run(client.send(payload))

    body = captured["body"]
    timestamp = captured["timestamp"]
    assert isinstance(body, bytes)
    assert timestamp == "1752832800"
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    assert captured["signature"] == f"sha256={expected}"
    assert json.loads(body)["external_event_id"] == "am_test"
    assert delivery.response_data["data"] == {"incident_id": "inc_001"}


def test_relay_returns_incidents_and_ignores_resolved_items() -> None:
    class Sender:
        async def send(self, payload: AgentAlertPayload) -> AgentAlertDelivery:
            assert payload.service_name == "inventory-service"
            return AgentAlertDelivery(
                status_code=200,
                response_data={"data": {"incident_id": "inc_001"}},
            )

    relay = AlertmanagerRelay(AlertmanagerAlertMapper("demo"), Sender())
    webhook = AlertmanagerWebhook(
        status="firing",
        alerts=(
            build_native_alert(),
            build_native_alert(status="resolved"),
        ),
    )

    result = asyncio.run(relay.relay(webhook))

    assert result.received == 2
    assert result.relayed == 1
    assert result.ignored == 1
    assert result.incident_ids == ("inc_001",)


def test_webhook_route_uses_configured_relay() -> None:
    class Relay:
        async def relay(self, payload: AlertmanagerWebhook) -> AlertmanagerRelayResult:
            assert len(payload.alerts) == 1
            return AlertmanagerRelayResult(
                received=1,
                relayed=1,
                ignored=0,
                incident_ids=("inc_001",),
            )

    previous = app.state.alertmanager_relay
    app.state.alertmanager_relay = Relay()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/integrations/alertmanager/webhook",
                json={
                    "status": "firing",
                    "alerts": [
                        {
                            "status": "firing",
                            "labels": {
                                "service_name": "inventory-service",
                                "severity": "P1",
                            },
                            "annotations": {"summary": "inventory timeout"},
                            "startsAt": "2026-07-18T10:00:00Z",
                            "fingerprint": "abcdef0123456789",
                        }
                    ],
                },
            )
    finally:
        app.state.alertmanager_relay = previous

    assert response.status_code == 200
    assert response.json() == {
        "received": 1,
        "relayed": 1,
        "ignored": 0,
        "incident_ids": ["inc_001"],
    }
