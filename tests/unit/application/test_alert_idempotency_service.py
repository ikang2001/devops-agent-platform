from datetime import UTC, datetime

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.application.services.alert_idempotency_service import (
    AlertIdempotencyService,
)
from devops_agent_platform.domain.enums import AlertSeverity
from devops_agent_platform.domain.models.alert import Alert


class InMemoryAlertLookup:
    """只实现当前应用服务需要的查询端口，避免单测连接数据库。"""

    def __init__(self, existing: Alert | None) -> None:
        self._existing = existing

    async def save(self, alert: Alert) -> None:
        raise AssertionError("idempotency check must not write data")

    async def get_by_external_event_id(
        self,
        tenant_id: str,
        source: str,
        external_event_id: str,
    ) -> Alert | None:
        if self._existing is None:
            return None
        if (
            self._existing.tenant_id == tenant_id
            and self._existing.source == source
            and self._existing.external_event_id == external_event_id
        ):
            return self._existing
        return None


def build_command() -> ReceiveAlertCommand:
    """构造幂等检查使用的应用命令。"""
    return ReceiveAlertCommand(
        tenant_id="tenant_001",
        source="alertmanager",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        summary="Checkout error rate is above threshold",
        starts_at=datetime(2026, 6, 27, 8, 0, tzinfo=UTC),
        fingerprint="fp_checkout_error_rate",
        external_event_id="evt_001",
        trace_id="trc_001",
    )


def build_existing_alert() -> Alert:
    """构造已经持久化的同一上游事件。"""
    command = build_command()
    return Alert(
        alert_id="alt_existing_001",
        tenant_id=command.tenant_id,
        source=command.source,
        service_name=command.service_name,
        severity=command.severity,
        summary=command.summary,
        starts_at=command.starts_at,
        fingerprint=command.fingerprint,
        external_event_id=command.external_event_id,
    )


async def test_new_event_is_not_duplicate() -> None:
    service = AlertIdempotencyService(InMemoryAlertLookup(existing=None))

    result = await service.check(build_command())

    assert result.is_duplicate is False
    assert result.existing_alert_id is None


async def test_existing_event_returns_original_alert_id() -> None:
    existing = build_existing_alert()
    service = AlertIdempotencyService(InMemoryAlertLookup(existing=existing))

    result = await service.check(build_command())

    assert result.is_duplicate is True
    assert result.existing_alert_id == existing.alert_id
