from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.topology import TopologyGraph


@dataclass(frozen=True)
class CorrelationDecision:
    incident_id: str | None
    score: float
    reason: str
    is_primary: bool


class AlertCorrelationService:
    """告警风暴收敛：租户、环境、时间、类型和拓扑关系共同决定关联。"""

    def __init__(self, *, window: timedelta = timedelta(minutes=15)) -> None:
        if window <= timedelta(0):
            raise ValueError("window must be positive")
        self._window = window

    def correlate(
        self,
        alert: Alert,
        candidates: Iterable[Incident],
        *,
        topology: TopologyGraph | None = None,
    ) -> CorrelationDecision:
        best: tuple[float, Incident, str] | None = None
        for incident in candidates:
            score, reason = self._score(alert, incident, topology)
            if score <= 0:
                continue
            candidate = (score, incident, reason)
            if best is None or (score, incident.updated_at, incident.incident_id) > (
                best[0],
                best[1].updated_at,
                best[1].incident_id,
            ):
                best = candidate
        if best is None:
            return CorrelationDecision(None, 0.0, "NO_MATCH", True)
        score, incident, reason = best
        return CorrelationDecision(incident.incident_id, round(score, 6), reason, False)

    def _score(
        self, alert: Alert, incident: Incident, topology: TopologyGraph | None
    ) -> tuple[float, str]:
        if (
            alert.tenant_id != incident.tenant_id
            or alert.environment != "default"
            and alert.environment != getattr(incident, "environment", "default")
        ):
            return 0.0, "TENANT_OR_ENVIRONMENT_MISMATCH"
        if incident.status.value not in {"OPEN", "ANALYZING"}:
            return 0.0, "INCIDENT_NOT_ACTIVE"
        if (
            not incident.created_at - self._window
            <= alert.starts_at
            <= incident.updated_at + self._window
        ):
            return 0.0, "OUTSIDE_TIME_WINDOW"
        score = 0.45
        reasons = ["TIME_WINDOW"]
        if alert.service_name == incident.service_name:
            score += 0.35
            reasons.append("SERVICE_MATCH")
        elif topology is not None and self._topology_related(
            topology, alert.service_name, incident.service_name
        ):
            score += 0.18
            reasons.append("TOPOLOGY_DEPENDENCY")
        else:
            return 0.0, "SERVICE_UNRELATED"
        if alert.alert_type == incident.correlation_reason:
            score += 0.05
        if alert.severity.rank >= 30:
            score += 0.05
        return min(score, 1.0), "+".join(reasons)

    @staticmethod
    def _topology_related(graph: TopologyGraph, left: str, right: str) -> bool:
        left_id = f"service:{left}"
        right_id = f"service:{right}"
        return any(
            edge.source_node_id == left_id
            and edge.target_node_id == right_id
            or edge.source_node_id == right_id
            and edge.target_node_id == left_id
            for edge in graph.edges
        )


def primary_alert_score(alert: Alert, *, root_service: str | None = None) -> float:
    score = alert.severity.rank / 30
    if root_service and alert.service_name == root_service:
        score += 0.5
    return round(min(score, 1.0), 6)
