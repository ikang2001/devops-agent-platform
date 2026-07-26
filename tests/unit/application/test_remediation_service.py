from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.commands.remediation import (
    CreateRemediationPlanCommand,
    DecideRemediationPlanCommand,
    ExecuteRemediationPlanCommand,
    RollbackRemediationPlanCommand,
)
from devops_agent_platform.application.services.remediation_service import (
    RemediationApplicationService,
)
from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
    ToolRiskLevel,
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
from devops_agent_platform.domain.models.remediation import (
    RemediationStatus,
)
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.domain.policies.remediation import (
    RemediationExecutionPolicy,
)
from devops_agent_platform.ports.remediation import (
    RemediationActionDefinition,
    RemediationExecutionOutcome,
)

NOW = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)
EVIDENCE_TYPES = (
    EvidenceType.METRIC,
    EvidenceType.LOG,
    EvidenceType.TRACE,
    EvidenceType.RUNBOOK,
)


class FixedIdentifiers:
    def new_remediation_plan_id(self) -> str:
        return "rmp_001"

    def new_event_id(self) -> str:
        return "evt_001"


class RemediationRepository:
    def __init__(self) -> None:
        self.plan = None

    async def save(self, plan) -> None:
        if self.plan is not None:
            raise ConflictError("duplicate")
        self.plan = plan

    async def get_by_id(self, tenant_id, remediation_plan_id):
        if (
            self.plan is not None
            and self.plan.tenant_id == tenant_id
            and self.plan.remediation_plan_id == remediation_plan_id
        ):
            return self.plan
        return None

    async def get_by_create_idempotency_key_hash(
        self,
        tenant_id,
        idempotency_key_hash,
    ):
        if (
            self.plan is not None
            and self.plan.tenant_id == tenant_id
            and self.plan.create_idempotency_key_hash == idempotency_key_hash
        ):
            return self.plan
        return None

    async def replace(self, plan, expected_version) -> None:
        if self.plan is None or self.plan.version != expected_version:
            raise ConflictError("version conflict")
        self.plan = plan

    async def list_stale_execution(self, *, now, limit=50):
        if (
            self.plan is None
            or self.plan.status is not RemediationStatus.EXECUTING
            or self.plan.execution_lease_expires_at is None
            or self.plan.execution_lease_expires_at > now
        ):
            return []
        return [self.plan][:limit]

    async def list_stale_rollback(self, *, now, limit=50):
        if (
            self.plan is None
            or self.plan.status is not RemediationStatus.ROLLING_BACK
            or self.plan.rollback_lease_expires_at is None
            or self.plan.rollback_lease_expires_at > now
        ):
            return []
        return [self.plan][:limit]


class StaticRepository:
    def __init__(self, value) -> None:
        self.value = value

    async def get_by_id(self, tenant_id, identifier):
        if self.value.tenant_id == tenant_id:
            return self.value
        return None

    async def get_by_workflow_run(self, tenant_id, workflow_run_id):
        if (
            self.value.tenant_id == tenant_id
            and self.value.workflow_run_id == workflow_run_id
        ):
            return self.value
        return None

    async def list_by_workflow_run(
        self,
        tenant_id,
        workflow_run_id,
        *,
        limit,
    ):
        return [
            item
            for item in self.value
            if item.tenant_id == tenant_id and item.workflow_run_id == workflow_run_id
        ][:limit]


class OutboxRepository:
    def __init__(self) -> None:
        self.items = []

    async def add(self, event) -> None:
        self.items.append(event)


class FakeUnitOfWork:
    def __init__(self, workflow, report, evidence, plans, outbox) -> None:
        self.workflow_runs = StaticRepository(workflow)
        self.rca_reports = StaticRepository(report)
        self.evidence = StaticRepository(evidence)
        self.remediation_plans = plans
        self.outbox = outbox

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self):
        return None


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def dry_run(self, request):
        self.calls.append(("dry_run", request))
        return RemediationExecutionOutcome(
            succeeded=True,
            summary="Action and rollback are valid.",
        )

    async def execute(self, request):
        self.calls.append(("execute", request))
        return RemediationExecutionOutcome(
            succeeded=True,
            summary="Inventory worker restarted.",
        )

    async def rollback(self, request):
        self.calls.append(("rollback", request))
        return RemediationExecutionOutcome(
            succeeded=True,
            summary="Previous worker revision restored.",
        )


class StaticActionCatalog:
    def __init__(self) -> None:
        self.definition = RemediationActionDefinition(
            action_key="restart_inventory",
            rollback_action_key="restore_inventory_revision",
            risk_level=ToolRiskLevel.MEDIUM,
            expected_effect="Restore inventory availability.",
            allowed_targets=("inventory",),
        )

    def get(self, action_key):
        if action_key != self.definition.action_key:
            raise ResourceNotFound("Remediation action is not registered")
        return self.definition


class DriftedActionCatalog(StaticActionCatalog):
    def __init__(self) -> None:
        super().__init__()
        self.definition = RemediationActionDefinition(
            action_key="restart_inventory",
            rollback_action_key="restore_inventory_revision_v2",
            risk_level=ToolRiskLevel.HIGH,
            expected_effect="Restore inventory availability with a new rollout.",
            allowed_targets=("inventory",),
        )


def build_workflow() -> WorkflowRun:
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="operator_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_workflow_001",
        status=WorkflowRunStatus.SUCCEEDED,
        version=3,
        execution_attempts=1,
        step_count=4,
        created_at=NOW - timedelta(minutes=15),
        updated_at=NOW - timedelta(minutes=5),
        started_at=NOW - timedelta(minutes=10),
        ended_at=NOW - timedelta(minutes=5),
    )


def build_evidence() -> list[Evidence]:
    items = []
    for index, evidence_type in enumerate(EVIDENCE_TYPES, start=1):
        content, digest = build_evidence_content(
            {"type": evidence_type.value, "healthy": False}
        )
        items.append(
            Evidence(
                evidence_id=f"evd_00{index}",
                tenant_id="tenant_001",
                incident_id="inc_001",
                workflow_run_id="wfr_001",
                execution_attempt=1,
                step_id=f"step_{index}",
                tool_name=f"tool_{index}",
                tool_version="1",
                evidence_type=evidence_type,
                source="minishop",
                summary=f"{evidence_type.value} evidence",
                content_json=content,
                content_sha256=digest,
                confidence=0.9,
                collected_at=NOW - timedelta(minutes=6),
            )
        )
    return items


def build_report(evidence: list[Evidence]) -> RCAReport:
    type_counts = tuple(sorted((item.evidence_type.value, 1) for item in evidence))
    return RCAReport(
        report_id="rpt_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.CONFIRMED,
        title="Inventory timeout",
        summary="Inventory connection pool exhausted.",
        confidence=0.95,
        evidence_ids=tuple(item.evidence_id for item in evidence),
        evidence_type_counts=type_counts,
        recommendations=("Restart the inventory worker.",),
        generator_name="deterministic",
        generator_version="1",
        generated_at=NOW - timedelta(minutes=5),
    )


def build_command(evidence: list[Evidence], **overrides):
    values = {
        "tenant_id": "tenant_001",
        "workflow_run_id": "wfr_001",
        "action_key": "restart_inventory",
        "target": "inventory",
        "evidence_ids": tuple(item.evidence_id for item in evidence),
        "idempotency_key": "create-remediation-001",
        "requested_by": "admin_author",
        "trace_id": "trc_remediation_001",
    }
    values.update(overrides)
    return CreateRemediationPlanCommand(**values)


def build_service(*, execution_enabled: bool = True, action_catalog=None):
    evidence = build_evidence()
    plans = RemediationRepository()
    outbox = OutboxRepository()
    executor = RecordingExecutor()
    unit_of_work = FakeUnitOfWork(
        build_workflow(),
        build_report(evidence),
        evidence,
        plans,
        outbox,
    )
    service = RemediationApplicationService(
        unit_of_work_factory=lambda: unit_of_work,
        identifier_generator=FixedIdentifiers(),
        action_catalog=action_catalog or StaticActionCatalog(),
        executor=executor,
        policy=RemediationExecutionPolicy(
            execution_enabled=execution_enabled,
            allowed_tenants=frozenset({"tenant_001"}),
        ),
        lease_seconds=60,
        request_timeout_seconds=10,
        clock=lambda: NOW,
    )
    return service, evidence, plans, outbox, executor, unit_of_work


async def test_approved_plan_executes_and_rolls_back_with_stable_keys() -> None:
    service, evidence, plans, outbox, executor, _ = build_service()
    created = await service.create(build_command(evidence))
    approved = await service.decide(
        DecideRemediationPlanCommand(
            tenant_id="tenant_001",
            remediation_plan_id=created.remediation_plan_id,
            expected_version=created.version,
            approved=True,
            reason="Reviewed evidence and rollback.",
            idempotency_key="approve-remediation-001",
            requested_by="admin_reviewer",
            trace_id="trc_approve_001",
        )
    )
    executed = await service.execute(
        ExecuteRemediationPlanCommand(
            tenant_id="tenant_001",
            remediation_plan_id=created.remediation_plan_id,
            expected_version=approved.version,
            idempotency_key="execute-remediation-001",
            requested_by="admin_executor",
            trace_id="trc_execute_001",
        )
    )
    rolled_back = await service.rollback(
        RollbackRemediationPlanCommand(
            tenant_id="tenant_001",
            remediation_plan_id=created.remediation_plan_id,
            expected_version=executed.version,
            idempotency_key="rollback-remediation-001",
            requested_by="admin_executor",
            trace_id="trc_rollback_001",
        )
    )

    assert created.status == RemediationStatus.DRAFT.value
    assert created.expected_effect == "Restore inventory availability."
    assert created.risk == ToolRiskLevel.MEDIUM.value
    assert created.rollback_action_key == "restore_inventory_revision"
    assert executed.status == RemediationStatus.SUCCEEDED.value
    assert rolled_back.status == RemediationStatus.ROLLED_BACK.value
    assert plans.plan.version == 6
    assert [phase for phase, _ in executor.calls] == [
        "dry_run",
        "execute",
        "rollback",
    ]
    assert executor.calls[1][1].idempotency_key == "execute:rmp_001"
    assert executor.calls[2][1].idempotency_key == "rollback:rmp_001"
    assert [event.event_type for event in outbox.items] == [
        "remediation.plan.created",
        "remediation.plan.decided",
        "remediation.execution.started",
        "remediation.execution.completed",
        "remediation.rollback.started",
        "remediation.rollback.completed",
    ]
    assert [event.trace_id for event in outbox.items] == [
        "trc_remediation_001",
        "trc_approve_001",
        "trc_execute_001",
        "trc_execute_001",
        "trc_rollback_001",
        "trc_rollback_001",
    ]


async def test_evidence_gate_idempotency_and_kill_switch_are_enforced() -> None:
    service, evidence, plans, _, executor, _ = build_service(execution_enabled=False)
    with pytest.raises(ConflictError, match="cited by the report"):
        await service.create(
            build_command(
                evidence,
                evidence_ids=(
                    "evd_001",
                    "evd_002",
                    "evd_003",
                    "evd_999",
                ),
            )
        )

    command = build_command(evidence)
    first = await service.create(command)
    duplicate = await service.create(command)
    assert duplicate.is_duplicate is True
    assert len(executor.calls) == 1

    with pytest.raises(ConflictError, match="another remediation"):
        await service.create(
            build_command(
                evidence,
                evidence_ids=tuple(
                    reversed(tuple(item.evidence_id for item in evidence))
                ),
            )
        )

    with pytest.raises(ConflictError, match="author cannot approve"):
        await service.decide(
            DecideRemediationPlanCommand(
                tenant_id="tenant_001",
                remediation_plan_id=first.remediation_plan_id,
                expected_version=first.version,
                approved=True,
                reason="Self approval must be blocked.",
                idempotency_key="approve-remediation-self",
                requested_by="admin_author",
                trace_id="trc_approve_self",
            )
        )

    approved = await service.decide(
        DecideRemediationPlanCommand(
            tenant_id="tenant_001",
            remediation_plan_id=first.remediation_plan_id,
            expected_version=first.version,
            approved=True,
            reason="Reviewed.",
            idempotency_key="approve-remediation-002",
            requested_by="admin_reviewer",
            trace_id="trc_approve_002",
        )
    )
    with pytest.raises(ConflictError, match="kill switch"):
        await service.execute(
            ExecuteRemediationPlanCommand(
                tenant_id="tenant_001",
                remediation_plan_id=first.remediation_plan_id,
                expected_version=approved.version,
                idempotency_key="execute-remediation-002",
                requested_by="admin_executor",
                trace_id="trc_execute_002",
            )
        )
    assert plans.plan.status is RemediationStatus.APPROVED


async def test_target_allowlist_and_execution_audit_gate_are_rechecked() -> None:
    service, evidence, plans, _, executor, unit_of_work = build_service()
    with pytest.raises(AppValidationError, match="target is not allowed"):
        await service.create(build_command(evidence, target="other-service"))
    assert executor.calls == []

    created = await service.create(build_command(evidence))
    approved = await service.decide(
        DecideRemediationPlanCommand(
            tenant_id="tenant_001",
            remediation_plan_id=created.remediation_plan_id,
            expected_version=created.version,
            approved=True,
            reason="Reviewed.",
            idempotency_key="approve-remediation-audit",
            requested_by="admin_reviewer",
            trace_id="trc_approve_audit",
        )
    )
    unit_of_work.workflow_runs.value.audit_purged_at = NOW

    with pytest.raises(ConflictError, match="available audit evidence"):
        await service.execute(
            ExecuteRemediationPlanCommand(
                tenant_id="tenant_001",
                remediation_plan_id=created.remediation_plan_id,
                expected_version=approved.version,
                idempotency_key="execute-remediation-audit",
                requested_by="admin_executor",
                trace_id="trc_execute_audit",
            )
        )
    assert plans.plan.status is RemediationStatus.APPROVED
    assert [phase for phase, _ in executor.calls] == ["dry_run"]


async def test_unknown_action_and_definition_drift_are_blocked() -> None:
    catalog = StaticActionCatalog()
    service, evidence, plans, _, executor, _ = build_service(action_catalog=catalog)
    with pytest.raises(ResourceNotFound, match="not registered"):
        await service.create(build_command(evidence, action_key="restart_checkout"))
    assert executor.calls == []

    created = await service.create(build_command(evidence))
    approved = await service.decide(
        DecideRemediationPlanCommand(
            tenant_id="tenant_001",
            remediation_plan_id=created.remediation_plan_id,
            expected_version=created.version,
            approved=True,
            reason="Reviewed.",
            idempotency_key="approve-remediation-drift",
            requested_by="admin_reviewer",
            trace_id="trc_approve_drift",
        )
    )
    catalog.definition = DriftedActionCatalog().definition

    with pytest.raises(ConflictError, match="definition has changed"):
        await service.execute(
            ExecuteRemediationPlanCommand(
                tenant_id="tenant_001",
                remediation_plan_id=created.remediation_plan_id,
                expected_version=approved.version,
                idempotency_key="execute-remediation-drift",
                requested_by="admin_executor",
                trace_id="trc_execute_drift",
            )
        )
    assert plans.plan.status is RemediationStatus.APPROVED
    assert [phase for phase, _ in executor.calls] == ["dry_run"]


async def test_expired_execution_lease_is_reclaimed_to_failed() -> None:
    """租约过期后 reclaim 必须把 EXECUTING 收口为 FAILED，旧 owner 不能再 finish。"""
    service, evidence, plans, outbox, executor, _ = build_service()
    created = await service.create(build_command(evidence))
    approved = await service.decide(
        DecideRemediationPlanCommand(
            tenant_id="tenant_001",
            remediation_plan_id=created.remediation_plan_id,
            expected_version=created.version,
            approved=True,
            reason="Reviewed.",
            idempotency_key="approve-remediation-lease",
            requested_by="admin_reviewer",
            trace_id="trc_approve_lease",
        )
    )

    # 手动推进到 EXECUTING，并伪造已过期租约，模拟进程崩溃后的残留态。
    started = plans.plan.start_execution(
        requested_by="admin_executor",
        started_at=NOW - timedelta(minutes=5),
        lease_expires_at=NOW - timedelta(seconds=1),
        trace_id="trc_execute_lease",
        idempotency_key="execute-remediation-lease",
    )
    plans.plan = started

    reclaimed = await service.reclaim_stale()
    assert len(reclaimed) == 1
    assert reclaimed[0].status == RemediationStatus.FAILED.value
    assert plans.plan.status is RemediationStatus.FAILED
    assert plans.plan.execution_lease_expires_at is None
    assert "lease expired" in (plans.plan.execution_summary or "").lower()
    assert [event.event_type for event in outbox.items][-1] == (
        "remediation.execution.lease_expired"
    )
    assert [phase for phase, _ in executor.calls] == ["dry_run"]
    # 过期后禁止用旧 attempt 收口成功。
    with pytest.raises(ConflictError, match="not executing|fence"):
        plans.plan.finish_execution(
            succeeded=True,
            summary="late finish must be rejected",
            completed_at=NOW,
            expected_attempt=1,
            requested_by="admin_executor",
        )
    assert approved.status == RemediationStatus.APPROVED.value


@pytest.mark.parametrize("limit", [0, 1001, True, 1.5])
async def test_reclaim_rejects_invalid_batch_limit(limit: object) -> None:
    """显式调用也不能绕过 Worker 的批量上限。"""
    service, _, _, _, _, _ = build_service()

    with pytest.raises(AppValidationError, match="limit"):
        await service.reclaim_stale(limit=limit)  # type: ignore[arg-type]


async def test_stale_finish_is_rejected_after_owner_mismatch() -> None:
    """finish 必须匹配 owner 与 attempt，防止迟到回调覆盖 reclaim 结果。"""
    service, evidence, plans, _, _, _ = build_service()
    created = await service.create(build_command(evidence))
    await service.decide(
        DecideRemediationPlanCommand(
            tenant_id="tenant_001",
            remediation_plan_id=created.remediation_plan_id,
            expected_version=created.version,
            approved=True,
            reason="Reviewed.",
            idempotency_key="approve-remediation-fence",
            requested_by="admin_reviewer",
            trace_id="trc_approve_fence",
        )
    )
    started = plans.plan.start_execution(
        requested_by="admin_executor",
        started_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=60),
        trace_id="trc_execute_fence",
        idempotency_key="execute-remediation-fence",
    )
    with pytest.raises(ConflictError, match="owner fence"):
        started.finish_execution(
            succeeded=True,
            summary="wrong owner",
            completed_at=NOW,
            expected_attempt=1,
            requested_by="other_operator",
        )
    with pytest.raises(ConflictError, match="attempt fence"):
        started.finish_execution(
            succeeded=True,
            summary="wrong attempt",
            completed_at=NOW,
            expected_attempt=2,
            requested_by="admin_executor",
        )
