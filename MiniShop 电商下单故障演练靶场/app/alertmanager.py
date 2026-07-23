import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.agent_alerts import AgentAlertDelivery, AgentAlertPayload


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    status: str
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    fingerprint: str = Field(min_length=1, max_length=256)


class AlertmanagerWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str = "4"
    status: str
    receiver: str = ""
    group_labels: dict[str, str] = Field(default_factory=dict, alias="groupLabels")
    common_labels: dict[str, str] = Field(default_factory=dict, alias="commonLabels")
    common_annotations: dict[str, str] = Field(
        default_factory=dict,
        alias="commonAnnotations",
    )
    alerts: list[AlertmanagerAlert] = Field(default_factory=list, max_length=100)


class AgentAlertSender(Protocol):
    async def send(self, payload: AgentAlertPayload) -> AgentAlertDelivery: ...


@dataclass(frozen=True)
class AlertmanagerRelayResult:
    received: int
    relayed: int
    ignored: int
    incident_ids: tuple[str, ...]


class AlertmanagerAlertMapper:
    def __init__(self, default_tenant_id: str = "demo") -> None:
        if not _is_safe_identifier(default_tenant_id, 128):
            raise ValueError("default_tenant_id is invalid")
        self._default_tenant_id = default_tenant_id

    def map(self, alert: AlertmanagerAlert) -> AgentAlertPayload | None:
        if alert.status.lower() != "firing":
            return None
        if alert.starts_at.tzinfo is None:
            raise ValueError("Alertmanager startsAt must include a timezone")

        labels = alert.labels
        tenant_id = labels.get("tenant_id", self._default_tenant_id)
        service_name = labels.get("service_name") or labels.get("service")
        if service_name is None:
            raise ValueError("Alertmanager alert is missing service_name")
        summary = (
            alert.annotations.get("summary")
            or alert.annotations.get("description")
            or labels.get("alertname")
        )
        if summary is None:
            raise ValueError("Alertmanager alert is missing summary")

        return AgentAlertPayload(
            tenant_id=tenant_id,
            source="alertmanager",
            service_name=service_name,
            severity=_map_severity(labels.get("severity", "WARNING")),
            summary=" ".join(summary.splitlines()).strip(),
            starts_at=alert.starts_at,
            fingerprint=alert.fingerprint,
            external_event_id=_build_external_event_id(alert),
        )


class AlertmanagerRelay:
    def __init__(
        self,
        mapper: AlertmanagerAlertMapper,
        sender: AgentAlertSender,
    ) -> None:
        self._mapper = mapper
        self._sender = sender

    async def relay(self, webhook: AlertmanagerWebhook) -> AlertmanagerRelayResult:
        incident_ids: list[str] = []
        relayed = 0
        ignored = 0
        for alert in webhook.alerts:
            payload = self._mapper.map(alert)
            if payload is None:
                ignored += 1
                continue
            delivery = await self._sender.send(payload)
            relayed += 1
            incident_id = _extract_incident_id(delivery.response_data)
            if incident_id is not None:
                incident_ids.append(incident_id)
        return AlertmanagerRelayResult(
            received=len(webhook.alerts),
            relayed=relayed,
            ignored=ignored,
            incident_ids=tuple(incident_ids),
        )


def _map_severity(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"P0", "P1", "CRITICAL", "SEV0", "SEV1"}:
        return "CRITICAL"
    if normalized in {"P2", "P3", "WARNING", "WARN", "SEV2", "SEV3"}:
        return "WARNING"
    if normalized in {"P4", "INFO", "INFORMATIONAL", "SEV4"}:
        return "INFO"
    return "WARNING"


def _build_external_event_id(alert: AlertmanagerAlert) -> str:
    identity = f"{alert.fingerprint}|{alert.starts_at.isoformat()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]
    return f"am_{digest}"


def _extract_incident_id(response_data: dict[str, object]) -> str | None:
    data = response_data.get("data")
    if not isinstance(data, dict):
        return None
    incident_id = data.get("incident_id")
    return incident_id if isinstance(incident_id, str) else None


def _is_safe_identifier(value: str, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        and not any(character.isspace() for character in value)
    )
