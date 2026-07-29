from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.commands.rca_feedback import (
    CreateRCAFeedbackCommand,
)
from devops_agent_platform.application.queries.rca_feedback import (
    GetRCAFeedbackEvaluationCandidateQuery,
    ListRCAFeedbackQuery,
)
from devops_agent_platform.application.services.rca_feedback_service import (
    RCAFeedbackApplicationService,
)
from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
    RCAFeedbackVerdict,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.workflow_run import WorkflowRun

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


class FixedIdentifiers:
    def new_rca_feedback_id(self) -> str:
        return "rcf_001"

    def new_event_id(self) -> str:
        return "evt_001"


class FeedbackRepository:
    def __init__(self) -> None:
        self.items = []

    async def save(self, feedback) -> None:
        self.items.append(feedback)

    async def get_by_idempotency_key_hash(
        self,
        tenant_id,
        idempotency_key_hash,
    ):
        return next(
            (
                item
                for item in self.items
                if item.tenant_id == tenant_id
                and item.idempotency_key_hash == idempotency_key_hash
            ),
            None,
        )

    async def get_by_id(self, tenant_id, feedback_id):
        return next(
            (
                item
                for item in self.items
                if item.tenant_id == tenant_id
                and item.feedback_id == feedback_id
            ),
            None,
        )

    async def list_by_workflow_run(
        self,
        tenant_id,
        workflow_run_id,
        limit,
    ):
        return [
            item
            for item in reversed(self.items)
            if item.tenant_id == tenant_id and item.workflow_run_id == workflow_run_id
        ][:limit]


class StaticRepository:
    def __init__(self, value) -> None:
        self.value = value

    async def get_by_id(self, tenant_id, workflow_run_id):
        if (
            tenant_id == self.value.tenant_id
            and workflow_run_id == self.value.workflow_run_id
        ):
            return self.value
        return None

    async def get_by_workflow_run(self, tenant_id, workflow_run_id):
        if (
            tenant_id == self.value.tenant_id
            and workflow_run_id == self.value.workflow_run_id
        ):
            return self.value
        return None


class OutboxRepository:
    def __init__(self) -> None:
        self.items = []

    async def add(self, event) -> None:
        self.items.append(event)


class EvidenceRepository:
    def __init__(self, items) -> None:
        self.items = items
        self.requests = []

    async def list_by_ids(self, tenant_id, evidence_ids):
        self.requests.append((tenant_id, evidence_ids))
        return [
            item
            for item in self.items
            if item.tenant_id == tenant_id
            and item.evidence_id in evidence_ids
        ]


class FakeUnitOfWork:
    def __init__(self, workflow, report, feedback, outbox, evidence) -> None:
        self.workflow_runs = StaticRepository(workflow)
        self.rca_reports = StaticRepository(report)
        self.rca_feedback = feedback
        self.outbox = outbox
        self.evidence = evidence
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self):
        self.committed = True


def build_workflow(
    status: WorkflowRunStatus = WorkflowRunStatus.SUCCEEDED,
) -> WorkflowRun:
    started = NOW - timedelta(minutes=10)
    ended = (
        NOW - timedelta(minutes=5) if status is not WorkflowRunStatus.RUNNING else None
    )
    updated = ended or started
    lease_owner = "worker_001" if status is WorkflowRunStatus.RUNNING else None
    lease_expires_at = (
        NOW + timedelta(minutes=5) if status is WorkflowRunStatus.RUNNING else None
    )
    heartbeat_at = started if status is WorkflowRunStatus.RUNNING else None
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="operator_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_workflow_001",
        status=status,
        version=3 if status is WorkflowRunStatus.SUCCEEDED else 2,
        execution_attempts=1,
        step_count=2,
        created_at=NOW - timedelta(minutes=15),
        updated_at=updated,
        started_at=started,
        ended_at=ended,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        heartbeat_at=heartbeat_at,
    )


def build_report() -> RCAReport:
    return RCAReport(
        report_id="rpt_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.CONFIRMED,
        title="Inventory timeout",
        summary="Inventory pool exhausted.",
        confidence=0.95,
        evidence_ids=("evd_001",),
        evidence_type_counts=((EvidenceType.METRIC.value, 1),),
        recommendations=("Restart the inventory worker.",),
        generator_name="deterministic",
        generator_version="1",
        generated_at=NOW - timedelta(minutes=5),
    )


def build_evidence() -> Evidence:
    content, digest = build_evidence_content(
        {"pool": "inventory", "password": "must-not-export"}
    )
    return Evidence(
        evidence_id="evd_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id="metrics.query",
        tool_name="metrics.query",
        tool_version="v1",
        evidence_type=EvidenceType.METRIC,
        source="prometheus",
        summary="Inventory pool saturation increased.",
        content_json=content,
        content_sha256=digest,
        confidence=0.9,
        collected_at=NOW - timedelta(minutes=6),
    )


def build_command(**overrides) -> CreateRCAFeedbackCommand:
    values = {
        "tenant_id": "tenant_001",
        "workflow_run_id": "wfr_001",
        "verdict": RCAFeedbackVerdict.PARTIAL,
        "corrected_root_cause": "Inventory pool exhausted.",
        "missing_evidence_types": (EvidenceType.TRACE,),
        "unsafe_recommendation_indexes": (0,),
        "follow_up_label": "needs-runbook",
        "notes": "Confirm rollback before action.",
        "idempotency_key": "feedback-key-001",
        "requested_by": "admin_001",
        "trace_id": "trc_feedback_001",
    }
    values.update(overrides)
    return CreateRCAFeedbackCommand(**values)


def build_service(status=WorkflowRunStatus.SUCCEEDED):
    feedback = FeedbackRepository()
    outbox = OutboxRepository()
    evidence = EvidenceRepository([build_evidence()])
    unit_of_work = FakeUnitOfWork(
        build_workflow(status),
        build_report(),
        feedback,
        outbox,
        evidence,
    )
    service = RCAFeedbackApplicationService(
        unit_of_work_factory=lambda: unit_of_work,
        identifier_generator=FixedIdentifiers(),
        clock=lambda: NOW,
    )
    return service, feedback, outbox, unit_of_work


async def test_feedback_is_sanitized_persisted_and_audited_without_text() -> None:
    service, feedback, outbox, _ = build_service()

    result = await service.create(
        build_command(
            notes="password=secret-token\nConfirm rollback.",
        )
    )

    assert result.feedback_id == "rcf_001"
    assert "secret-token" not in result.notes
    assert len(feedback.items) == 1
    assert len(outbox.items) == 1
    payload = outbox.items[0].payload
    assert payload["verdict"] == "PARTIAL"
    assert "notes" not in payload
    assert "corrected_root_cause" not in payload


async def test_feedback_idempotency_replays_same_request_and_rejects_reuse() -> None:
    service, feedback, _, _ = build_service()
    command = build_command()

    first = await service.create(command)
    second = await service.create(command)

    assert first.is_duplicate is False
    assert second.is_duplicate is True
    assert len(feedback.items) == 1
    with pytest.raises(ConflictError, match="another RCA feedback"):
        await service.create(build_command(notes="Different finding."))


async def test_feedback_requires_successful_workflow_and_valid_report_index() -> None:
    running_service, _, _, _ = build_service(WorkflowRunStatus.RUNNING)
    with pytest.raises(ConflictError, match="succeeded workflow"):
        await running_service.create(build_command())

    service, _, _, _ = build_service()
    with pytest.raises(AppValidationError, match="out of range"):
        await service.create(build_command(unsafe_recommendation_indexes=(1,)))


async def test_feedback_history_is_tenant_scoped_and_bounded() -> None:
    service, _, _, _ = build_service()
    await service.create(build_command())

    items = await service.list(
        ListRCAFeedbackQuery(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            limit=10,
        )
    )

    assert [item.feedback_id for item in items] == ["rcf_001"]


async def test_feedback_exports_review_required_evaluation_candidate() -> None:
    service, _, _, unit_of_work = build_service()
    created = await service.create(build_command(notes="private reviewer note"))
    unit_of_work.rca_reports.value = replace(
        unit_of_work.rca_reports.value,
        summary="Inventory pool exhausted; password=baseline-secret.",
        recommendations=("Use token=recommendation-secret to restart.",),
    )
    unit_of_work.evidence.items[0] = replace(
        unit_of_work.evidence.items[0],
        summary="Pool saturated with password=evidence-secret.",
    )
    unit_of_work.evidence.items.append(
        replace(
            build_evidence(),
            evidence_id="evd_002",
            step_id="logs.query",
            tool_name="logs.query",
            evidence_type=EvidenceType.LOG,
            source="loki",
            summary="Inventory timeout errors increased.",
        )
    )
    unit_of_work.rca_reports.value = replace(
        unit_of_work.rca_reports.value,
        evidence_ids=("evd_002", "evd_001"),
        evidence_type_counts=(("LOG", 1), ("METRIC", 1)),
    )

    candidate = await service.get_evaluation_candidate(
        GetRCAFeedbackEvaluationCandidateQuery(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            feedback_id=created.feedback_id,
        )
    )
    payload = candidate.to_dict()

    assert payload["schema_version"] == 1
    assert payload["review_required"] is True
    assert payload["expected_root_cause"] == (
        "Inventory pool exhausted."
    )
    assert payload["required_evidence_ids"] == ["evd_002", "evd_001"]
    assert [item["evidence_id"] for item in payload["evidence"]] == [
        "evd_002",
        "evd_001",
    ]
    assert payload["evidence"][0]["evidence_type"] == "LOG"
    assert unit_of_work.evidence.requests == [
        ("tenant_001", ("evd_002", "evd_001"))
    ]
    serialized = str(payload)
    assert "must-not-export" not in serialized
    assert "private reviewer note" not in serialized
    assert "admin_001" not in serialized
    assert "trc_feedback_001" not in serialized
    assert "baseline-secret" not in serialized
    assert "recommendation-secret" not in serialized
    assert "evidence-secret" not in serialized


async def test_evaluation_candidate_requires_matching_complete_evidence() -> None:
    service, _, _, unit_of_work = build_service()
    created = await service.create(build_command())
    query = GetRCAFeedbackEvaluationCandidateQuery(
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        feedback_id=created.feedback_id,
    )
    unit_of_work.evidence.items = []

    with pytest.raises(ConflictError, match="every referenced evidence"):
        await service.get_evaluation_candidate(query)

    unit_of_work.workflow_runs.value = build_workflow(
        WorkflowRunStatus.RUNNING
    )
    with pytest.raises(ConflictError, match="succeeded workflow"):
        await service.get_evaluation_candidate(query)


async def test_evaluation_candidate_rejects_purged_audit_evidence() -> None:
    service, _, _, unit_of_work = build_service()
    created = await service.create(build_command())
    unit_of_work.workflow_runs.value = replace(
        unit_of_work.workflow_runs.value,
        audit_purged_at=NOW,
    )

    with pytest.raises(ConflictError, match="available audit evidence"):
        await service.get_evaluation_candidate(
            GetRCAFeedbackEvaluationCandidateQuery(
                tenant_id="tenant_001",
                workflow_run_id="wfr_001",
                feedback_id=created.feedback_id,
            )
        )


async def test_evaluation_candidate_rejects_feedback_workflow_mismatch() -> None:
    service, feedback, _, _ = build_service()
    created = await service.create(build_command())
    feedback.items[0] = replace(
        feedback.items[0],
        workflow_run_id="wfr_other",
    )

    with pytest.raises(ResourceNotFound, match="RCA feedback not found"):
        await service.get_evaluation_candidate(
            GetRCAFeedbackEvaluationCandidateQuery(
                tenant_id="tenant_001",
                workflow_run_id="wfr_001",
                feedback_id=created.feedback_id,
            )
        )


async def test_evaluation_candidate_rejects_feedback_report_mismatch() -> None:
    service, _, _, unit_of_work = build_service()
    created = await service.create(build_command())
    unit_of_work.rca_reports.value = replace(
        unit_of_work.rca_reports.value,
        report_id="rpt_other",
    )

    with pytest.raises(ConflictError, match="report is unavailable"):
        await service.get_evaluation_candidate(
            GetRCAFeedbackEvaluationCandidateQuery(
                tenant_id="tenant_001",
                workflow_run_id="wfr_001",
                feedback_id=created.feedback_id,
            )
        )


async def test_evaluation_candidate_rejects_evidence_attempt_mismatch() -> None:
    service, _, _, unit_of_work = build_service()
    created = await service.create(build_command())
    unit_of_work.evidence.items[0] = replace(
        unit_of_work.evidence.items[0],
        execution_attempt=2,
    )

    with pytest.raises(ConflictError, match="report execution"):
        await service.get_evaluation_candidate(
            GetRCAFeedbackEvaluationCandidateQuery(
                tenant_id="tenant_001",
                workflow_run_id="wfr_001",
                feedback_id=created.feedback_id,
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tenant_id", " tenant_001"),
        ("workflow_run_id", "wfr_001\x7f"),
        ("feedback_id", "rcf 001"),
    ),
)
def test_evaluation_candidate_query_rejects_dirty_identity(
    field: str,
    value: str,
) -> None:
    values = {
        "tenant_id": "tenant_001",
        "workflow_run_id": "wfr_001",
        "feedback_id": "rcf_001",
    }
    values[field] = value

    with pytest.raises(AppValidationError, match=f"{field} is invalid"):
        GetRCAFeedbackEvaluationCandidateQuery(**values)
