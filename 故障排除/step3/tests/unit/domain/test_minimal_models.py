from datetime import UTC, datetime

from devops_agent_platform.domain.enums import (
    AlertSeverity,
    EvidenceType,
    IncidentStatus,
    ToolInvocationStatus,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.models import (
    Alert,
    Evidence,
    Incident,
    ToolInvocation,
    WorkflowRun,
)


def test_minimal_domain_models_are_constructable() -> None:
    now = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)

    alert = Alert(
        alert_id="alert-001",
        tenant_id="tenant-a",
        source="alertmanager",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        summary="5xx error rate is high",
        starts_at=now,
        fingerprint="fp-001",
    )
    incident = Incident(
        incident_id="inc-001",
        tenant_id="tenant-a",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.OPEN,
        title="checkout-api 5xx spike",
        created_at=now,
        updated_at=now,
    )
    evidence = Evidence(
        evidence_id="ev-001",
        incident_id=incident.incident_id,
        evidence_type=EvidenceType.LOG,
        source="loki",
        summary="error pattern detected",
        confidence=0.8,
        collected_at=now,
    )
    workflow = WorkflowRun(
        workflow_run_id="wf-001",
        incident_id=incident.incident_id,
        status=WorkflowRunStatus.PENDING,
        started_at=now,
        ended_at=None,
        step_count=0,
    )
    invocation = ToolInvocation(
        invocation_id="tool-001",
        incident_id=incident.incident_id,
        tool_name="logs.query",
        tool_version="v1",
        status=ToolInvocationStatus.PENDING,
        input_summary="query recent errors",
        output_summary=None,
        latency_ms=None,
        error_message=None,
    )

    assert alert.alert_id == "alert-001"
    assert incident.status is IncidentStatus.OPEN
    assert evidence.evidence_type is EvidenceType.LOG
    assert workflow.step_count == 0
    assert invocation.status is ToolInvocationStatus.PENDING

