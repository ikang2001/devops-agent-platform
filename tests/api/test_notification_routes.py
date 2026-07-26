from datetime import UTC, datetime

from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.notification_service import (
    NotificationDeliveryView,
)
from devops_agent_platform.bootstrap.app import create_app

URL = "/api/v1/admin/tenants/tenant_001/workflow-runs/wfr_001/notifications"


class StaticAuthenticator:
    def __init__(self, scopes) -> None:
        self.scopes = scopes

    async def authenticate(self, bearer_token):
        assert bearer_token == "admin-token"
        return AdministratorPrincipal(
            admin_id="admin_001",
            scopes=self.scopes,
            tenant_ids=frozenset({"tenant_001"}),
        )


class RecordingService:
    def __init__(self) -> None:
        self.commands = []

    async def send(self, command):
        self.commands.append(command)
        return NotificationDeliveryView(
            tenant_id=command.tenant_id,
            workflow_run_id=command.workflow_run_id,
            target_system=command.target_system,
            external_reference=command.idempotency_key,
            trace_id=command.trace_id,
            delivered_at=datetime(2026, 7, 23, 15, 0, tzinfo=UTC),
        )


def build_client(scopes):
    service = RecordingService()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=StaticAuthenticator(scopes),
    )
    app.state.notification_service = service
    return TestClient(app), service


def test_notification_route_uses_scope_identity_and_idempotency() -> None:
    client, service = build_client(frozenset({"notifications:send"}))

    response = client.post(
        URL,
        headers={
            "Authorization": "Bearer admin-token",
            "Idempotency-Key": "notification-key-001",
            "X-Trace-Id": "trc_notification_001",
        },
        json={"target_system": "pagerduty"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["target_system"] == "pagerduty"
    command = service.commands[0]
    assert command.requested_by == "admin_001"
    assert command.idempotency_key == "notification-key-001"


def test_notification_route_rejects_unknown_target_and_missing_scope() -> None:
    client, service = build_client(frozenset({"rca:read"}))
    headers = {
        "Authorization": "Bearer admin-token",
        "Idempotency-Key": "notification-key-001",
    }

    forbidden = client.post(
        URL,
        headers=headers,
        json={"target_system": "slack"},
    )
    invalid = client.post(
        URL,
        headers=headers,
        json={"target_system": "email"},
    )

    assert forbidden.status_code == 403
    assert invalid.status_code == 422
    assert service.commands == []
