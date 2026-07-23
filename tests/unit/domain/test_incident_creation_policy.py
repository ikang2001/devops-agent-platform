from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.domain.enums import (
    AlertSeverity,
    IncidentCreationAction,
    IncidentDecisionReason,
    IncidentStatus,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.policies.incident_creation import (
    IncidentCreationDecision,
    IncidentCreationPolicy,
    IncidentCreationPolicyConfig,
)

BASE_TIME = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)


def build_alert(
    severity: AlertSeverity = AlertSeverity.WARNING,
    starts_at: datetime = BASE_TIME,
    tenant_id: str = "tenant_001",
    service_name: str = "checkout-api",
) -> Alert:
    """构造策略测试使用的告警事实。"""
    return Alert(
        alert_id="alt_policy_001",
        tenant_id=tenant_id,
        source="alertmanager",
        service_name=service_name,
        severity=severity,
        summary="Checkout error rate is above threshold",
        starts_at=starts_at,
        fingerprint="fp_checkout_error_rate",
        external_event_id="evt_policy_001",
    )


def build_incident(
    incident_id: str = "inc_policy_001",
    status: IncidentStatus = IncidentStatus.OPEN,
    created_at: datetime = BASE_TIME - timedelta(minutes=5),
    updated_at: datetime = BASE_TIME - timedelta(minutes=1),
    tenant_id: str = "tenant_001",
    service_name: str = "checkout-api",
) -> Incident:
    """构造策略候选事故。"""
    return Incident(
        incident_id=incident_id,
        tenant_id=tenant_id,
        service_name=service_name,
        severity=AlertSeverity.WARNING,
        status=status,
        title="Checkout degradation",
        created_at=created_at,
        updated_at=updated_at,
    )


def test_below_threshold_alert_is_ignored_without_consuming_candidates() -> None:
    consumed = False

    def candidates():
        nonlocal consumed
        consumed = True
        yield build_incident()

    decision = IncidentCreationPolicy().decide(
        build_alert(severity=AlertSeverity.INFO),
        candidates(),
    )

    assert decision.action is IncidentCreationAction.IGNORE
    assert decision.reason is IncidentDecisionReason.BELOW_SEVERITY_THRESHOLD
    assert consumed is False


def test_warning_without_matching_incident_creates_new_incident() -> None:
    decision = IncidentCreationPolicy().decide(build_alert(), [])

    assert decision.action is IncidentCreationAction.CREATE
    assert decision.reason is IncidentDecisionReason.NO_MATCHING_ACTIVE_INCIDENT
    assert decision.matched_incident_id is None


def test_matching_active_incident_is_attached() -> None:
    incident = build_incident()

    decision = IncidentCreationPolicy().decide(build_alert(), [incident])

    assert decision.action is IncidentCreationAction.ATTACH
    assert decision.reason is IncidentDecisionReason.ACTIVE_INCIDENT_MATCHED
    assert decision.matched_incident_id == incident.incident_id


@pytest.mark.parametrize(
    "incident",
    [
        build_incident(tenant_id="another_tenant"),
        build_incident(service_name="payments-api"),
        build_incident(status=IncidentStatus.RESOLVED),
        build_incident(status=IncidentStatus.CLOSED),
        build_incident(
            created_at=BASE_TIME - timedelta(minutes=20),
            updated_at=BASE_TIME - timedelta(minutes=16),
        ),
    ],
)
def test_non_matching_candidate_creates_new_incident(
    incident: Incident,
) -> None:
    decision = IncidentCreationPolicy().decide(build_alert(), [incident])

    assert decision.action is IncidentCreationAction.CREATE


def test_correlation_window_boundary_is_inclusive() -> None:
    incident = build_incident(
        created_at=BASE_TIME - timedelta(minutes=20),
        updated_at=BASE_TIME - timedelta(minutes=15),
    )

    decision = IncidentCreationPolicy().decide(build_alert(), [incident])

    assert decision.action is IncidentCreationAction.ATTACH


def test_clock_skew_tolerance_accepts_slightly_early_alert() -> None:
    incident = build_incident(
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )
    alert = build_alert(starts_at=BASE_TIME - timedelta(minutes=2))

    decision = IncidentCreationPolicy().decide(alert, [incident])

    assert decision.action is IncidentCreationAction.ATTACH


def test_most_recent_candidate_wins_with_deterministic_tie_breaker() -> None:
    first = build_incident(incident_id="inc_a")
    second = build_incident(incident_id="inc_b")
    older = build_incident(
        incident_id="inc_z",
        updated_at=BASE_TIME - timedelta(minutes=2),
    )

    decision = IncidentCreationPolicy().decide(
        build_alert(),
        [first, older, second],
    )

    assert decision.matched_incident_id == "inc_b"


def test_custom_critical_threshold_ignores_warning() -> None:
    policy = IncidentCreationPolicy(
        IncidentCreationPolicyConfig(
            minimum_severity=AlertSeverity.CRITICAL,
        )
    )

    decision = policy.decide(build_alert(severity=AlertSeverity.WARNING), [])

    assert decision.action is IncidentCreationAction.IGNORE


@pytest.mark.parametrize(
    "values",
    [
        {"minimum_severity": "WARNING"},
        {"correlation_window": timedelta(0)},
        {"clock_skew_tolerance": timedelta(seconds=-1)},
        {"active_statuses": frozenset()},
        {"active_statuses": frozenset({"OPEN"})},
    ],
)
def test_invalid_policy_config_is_rejected(values: dict[str, object]) -> None:
    with pytest.raises(AppValidationError):
        IncidentCreationPolicyConfig(**values)  # type: ignore[arg-type]


def test_attach_decision_requires_incident_id() -> None:
    with pytest.raises(AppValidationError):
        IncidentCreationDecision(
            action=IncidentCreationAction.ATTACH,
            reason=IncidentDecisionReason.ACTIVE_INCIDENT_MATCHED,
        )


def test_decision_rejects_inconsistent_action_and_reason() -> None:
    with pytest.raises(AppValidationError, match="inconsistent"):
        IncidentCreationDecision(
            action=IncidentCreationAction.CREATE,
            reason=IncidentDecisionReason.BELOW_SEVERITY_THRESHOLD,
        )
