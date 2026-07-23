from datetime import UTC, datetime

from devops_agent_platform.domain.enums import (
    AlertSeverity,
    EvidenceType,
    IncidentStatus,
    ToolInvocationStatus,
    ToolRiskLevel,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.models import (
    Alert,
    Evidence,
    Incident,
    ToolInvocation,
    WorkflowRun,
)
from devops_agent_platform.domain.models.evidence import build_evidence_content


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
        external_event_id="evt-001",
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
    content_json, content_sha256 = build_evidence_content(
        {"message": "error pattern"}
    )
    evidence = Evidence(
        evidence_id="ev-001",
        tenant_id="tenant-a",
        incident_id=incident.incident_id,
        workflow_run_id="wf-001",
        execution_attempt=1,
        step_id="logs.query",
        tool_name="logs.query",
        tool_version="v1",
        evidence_type=EvidenceType.LOG,
        source="loki",
        summary="error pattern detected",
        content_json=content_json,
        content_sha256=content_sha256,
        confidence=0.8,
        collected_at=now,
    )
    workflow = WorkflowRun(
        workflow_run_id="wf-001",
        tenant_id="tenant-a",
        incident_id=incident.incident_id,
        operator_id="user-001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc-001",
        status=WorkflowRunStatus.PENDING,
        created_at=now,
        updated_at=now,
        started_at=None,
        ended_at=None,
        step_count=0,
    )
    invocation = ToolInvocation(
        invocation_id="c" * 64,
        tenant_id="tenant-a",
        incident_id=incident.incident_id,
        workflow_run_id="wf-001",
        execution_attempt=1,
        step_id="collect.logs",
        operator_id="user-001",
        trace_id="trc-001",
        tool_name="logs.query",
        tool_version="v1",
        risk_level=ToolRiskLevel.LOW,
        status=ToolInvocationStatus.SUCCEEDED,
        input_summary="payload_fields=2",
        input_sha256="d" * 64,
        output_summary="query completed",
        output_sha256="e" * 64,
        latency_ms=12,
        error_code=None,
        started_at=now,
        ended_at=now,
    )

    assert alert.alert_id == "alert-001"
    assert incident.status is IncidentStatus.OPEN
    assert evidence.evidence_type is EvidenceType.LOG
    assert workflow.step_count == 0
    assert invocation.status is ToolInvocationStatus.SUCCEEDED
