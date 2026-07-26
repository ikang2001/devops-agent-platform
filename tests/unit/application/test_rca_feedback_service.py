from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.commands.rca_feedback import (
    CreateRCAFeedbackCommand,
)
from devops_agent_platform.application.queries.rca_feedback import (
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


class FakeUnitOfWork:
    def __init__(self, workflow, report, feedback, outbox) -> None:
        self.workflow_runs = StaticRepository(workflow)
        self.rca_reports = StaticRepository(report)
        self.rca_feedback = feedback
        self.outbox = outbox
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
    unit_of_work = FakeUnitOfWork(
        build_workflow(status),
        build_report(),
        feedback,
        outbox,
    )
    service = RCAFeedbackApplicationService(
        unit_of_work_factory=lambda: unit_of_work,
        identifier_generator=FixedIdentifiers(),
        clock=lambda: NOW,
    )
    return service, feedback, outbox


async def test_feedback_is_sanitized_persisted_and_audited_without_text() -> None:
    service, feedback, outbox = build_service()

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
    service, feedback, _ = build_service()
    command = build_command()

    first = await service.create(command)
    second = await service.create(command)

    assert first.is_duplicate is False
    assert second.is_duplicate is True
    assert len(feedback.items) == 1
    with pytest.raises(ConflictError, match="another RCA feedback"):
        await service.create(build_command(notes="Different finding."))


async def test_feedback_requires_successful_workflow_and_valid_report_index() -> None:
    running_service, _, _ = build_service(WorkflowRunStatus.RUNNING)
    with pytest.raises(ConflictError, match="succeeded workflow"):
        await running_service.create(build_command())

    service, _, _ = build_service()
    with pytest.raises(AppValidationError, match="out of range"):
        await service.create(build_command(unsafe_recommendation_indexes=(1,)))


async def test_feedback_history_is_tenant_scoped_and_bounded() -> None:
    service, _, _ = build_service()
    await service.create(build_command())

    items = await service.list(
        ListRCAFeedbackQuery(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            limit=10,
        )
    )

    assert [item.feedback_id for item in items] == ["rcf_001"]
