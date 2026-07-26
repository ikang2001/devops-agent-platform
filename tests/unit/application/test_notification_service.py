from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from devops_agent_platform.application.commands.notifications import (
    SendWorkflowNotificationCommand,
)
from devops_agent_platform.application.exceptions import (
    NotificationGatewayError,
)
from devops_agent_platform.application.services.notification_service import (
    NotificationApplicationService,
)
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    WorkflowRunStatus,
)
from devops_agent_platform.ports.notifications import (
    NotificationDeliveryOutcome,
)

NOW = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)


class StaticRepository:
    def __init__(self, value) -> None:
        self.value = value

    async def get_by_id(self, *args):
        del args
        return self.value

    async def get_by_workflow_run(self, *args):
        del args
        return self.value


class OutboxRepository:
    def __init__(self) -> None:
        self.items = []

    async def add(self, event) -> None:
        self.items.append(event)


class FakeUnitOfWork:
    def __init__(self, outbox) -> None:
        self.workflow_runs = StaticRepository(
            SimpleNamespace(
                workflow_run_id="wfr_001",
                incident_id="inc_001",
                status=WorkflowRunStatus.SUCCEEDED,
                audit_purged_at=None,
            )
        )
        self.rca_reports = StaticRepository(
            SimpleNamespace(
                title="Inventory timeout",
                summary="Inventory database timed out.",
                recommendations=("Check database pool.",),
            )
        )
        self.incidents = StaticRepository(
            SimpleNamespace(
                incident_id="inc_001",
                service_name="inventory",
                severity=AlertSeverity.CRITICAL,
            )
        )
        self.outbox = outbox

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self):
        return None


class RecordingGateway:
    def __init__(self, error=None) -> None:
        self.error = error
        self.calls = []

    async def send(self, target_system, message):
        self.calls.append((target_system, message))
        if self.error is not None:
            raise self.error
        return NotificationDeliveryOutcome(
            provider=target_system,
            external_reference=message.idempotency_key,
        )


class FixedIdentifiers:
    def __init__(self) -> None:
        self.index = 0

    def new_event_id(self) -> str:
        self.index += 1
        return f"evt_{self.index:03d}"


def command() -> SendWorkflowNotificationCommand:
    return SendWorkflowNotificationCommand(
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        target_system="slack",
        idempotency_key="notification-key-001",
        requested_by="admin_001",
        trace_id="trc_notification_001",
    )


def build_service(error=None):
    outbox = OutboxRepository()
    gateway = RecordingGateway(error)
    service = NotificationApplicationService(
        unit_of_work_factory=lambda: FakeUnitOfWork(outbox),
        gateway=gateway,
        identifier_generator=FixedIdentifiers(),
        clock=lambda: NOW,
    )
    return service, gateway, outbox


async def test_service_derives_notification_from_persisted_rca_and_audits() -> None:
    service, gateway, outbox = build_service()

    result = await service.send(command())

    assert result.target_system == "slack"
    sent = gateway.calls[0][1]
    assert sent.title == "Inventory timeout"
    assert sent.summary == "Inventory database timed out."
    assert outbox.items[0].event_type == "rca.notification.delivered"
    payload = outbox.items[0].payload
    assert payload["succeeded"] is True
    assert "external_reference" not in payload
    assert len(payload["external_reference_sha256"]) == 64


async def test_gateway_failure_is_audited_before_error_propagates() -> None:
    service, _, outbox = build_service(NotificationGatewayError("provider unavailable"))

    with pytest.raises(NotificationGatewayError):
        await service.send(command())

    assert outbox.items[0].event_type == "rca.notification.failed"
    assert outbox.items[0].payload["succeeded"] is False
