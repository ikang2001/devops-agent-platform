from datetime import UTC, datetime

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.domain.enums import AlertSeverity
from devops_agent_platform.interfaces.http.dto import AlertWebhookRequest


def test_receive_alert_command_has_no_fastapi_dependency() -> None:
    command = ReceiveAlertCommand(
        tenant_id="tenant-a",
        source="alertmanager",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        summary="5xx error rate is high",
        starts_at=datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        fingerprint="fp-001",
        trace_id="trc_001",
    )

    assert command.trace_id == "trc_001"
    assert command.severity is AlertSeverity.CRITICAL
    assert "fastapi" not in ReceiveAlertCommand.__module__


def test_http_dto_can_convert_to_application_command() -> None:
    payload = AlertWebhookRequest(
        tenant_id="tenant-a",
        source="alertmanager",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        summary="5xx error rate is high",
        starts_at=datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
        fingerprint="fp-001",
    )

    command = payload.to_command(trace_id="trc_001")

    assert isinstance(command, ReceiveAlertCommand)
    assert command.tenant_id == "tenant-a"
    assert command.trace_id == "trc_001"

