import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from devops_agent_platform.application.commands.remediation import (
    CreateRemediationPlanCommand,
    DecideRemediationPlanCommand,
    ExecuteRemediationPlanCommand,
    RollbackRemediationPlanCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.queries.remediation import (
    GetRemediationPlanQuery,
)
from devops_agent_platform.domain.enums import EvidenceType, WorkflowRunStatus
from devops_agent_platform.domain.exceptions import (
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.remediation import (
    RemediationPlan,
    RemediationRisk,
    RemediationStatus,
)
from devops_agent_platform.domain.policies.remediation import (
    RemediationExecutionPolicy,
)
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.remediation import (
    RemediationActionCatalogPort,
    RemediationExecutionRequest,
    RemediationExecutorPort,
)
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]
_REQUIRED_EVIDENCE_TYPES = frozenset(
    {
        EvidenceType.METRIC,
        EvidenceType.LOG,
        EvidenceType.TRACE,
        EvidenceType.RUNBOOK,
    }
)
_STALE_EXECUTION_SUMMARY = (
    "Execution lease expired before a fenced completion was recorded."
)
_STALE_ROLLBACK_SUMMARY = (
    "Rollback lease expired before a fenced completion was recorded."
)


@dataclass(frozen=True)
class RemediationPlanView:
    """不暴露幂等摘要的管理端修复计划视图。"""

    remediation_plan_id: str
    tenant_id: str
    workflow_run_id: str
    incident_id: str
    action_key: str
    target: str
    expected_effect: str
    risk: str
    rollback_action_key: str
    evidence_ids: tuple[str, ...]
    dry_run_summary: str
    status: str
    version: int
    created_by: str
    created_at: datetime
    decided_by: str | None
    decision_reason: str | None
    decided_at: datetime | None
    execution_summary: str | None
    execution_started_at: datetime | None
    executed_at: datetime | None
    executed_by: str | None
    execution_attempt: int
    execution_lease_expires_at: datetime | None
    rollback_summary: str | None
    rollback_started_at: datetime | None
    rolled_back_at: datetime | None
    rolled_back_by: str | None
    rollback_attempt: int
    rollback_lease_expires_at: datetime | None
    trace_id: str
    is_duplicate: bool = False

    @classmethod
    def from_domain(
        cls,
        plan: RemediationPlan,
        *,
        is_duplicate: bool = False,
    ) -> "RemediationPlanView":
        return cls(
            remediation_plan_id=plan.remediation_plan_id,
            tenant_id=plan.tenant_id,
            workflow_run_id=plan.workflow_run_id,
            incident_id=plan.incident_id,
            action_key=plan.action_key,
            target=plan.target,
            expected_effect=plan.expected_effect,
            risk=plan.risk.value,
            rollback_action_key=plan.rollback_action_key,
            evidence_ids=plan.evidence_ids,
            dry_run_summary=plan.dry_run_summary,
            status=plan.status.value,
            version=plan.version,
            created_by=plan.created_by,
            created_at=plan.created_at,
            decided_by=plan.decided_by,
            decision_reason=plan.decision_reason,
            decided_at=plan.decided_at,
            execution_summary=plan.execution_summary,
            execution_started_at=plan.execution_started_at,
            executed_at=plan.executed_at,
            executed_by=plan.executed_by,
            execution_attempt=plan.execution_attempt,
            execution_lease_expires_at=plan.execution_lease_expires_at,
            rollback_summary=plan.rollback_summary,
            rollback_started_at=plan.rollback_started_at,
            rolled_back_at=plan.rolled_back_at,
            rolled_back_by=plan.rolled_back_by,
            rollback_attempt=plan.rollback_attempt,
            rollback_lease_expires_at=plan.rollback_lease_expires_at,
            trace_id=plan.trace_id,
            is_duplicate=is_duplicate,
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_ids"] = list(self.evidence_ids)
        for name in (
            "created_at",
            "decided_at",
            "execution_started_at",
            "executed_at",
            "execution_lease_expires_at",
            "rollback_started_at",
            "rolled_back_at",
            "rollback_lease_expires_at",
        ):
            value = result[name]
            result[name] = value.isoformat() if value is not None else None
        return result


class RemediationApplicationService:
    """把可信 RCA 证据转成须人工批准、可回滚的预注册动作。

    执行与回滚采用 claim → timeout 调用 → fence 收口：先持久化租约，
    再调用外部控制器；迟到的 finish 必须匹配 owner 与 attempt。
    过期租约通过 reclaim_stale_* 收口为 FAILED / ROLLBACK_FAILED。
    """

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        identifier_generator: IdentifierGeneratorPort,
        action_catalog: RemediationActionCatalogPort,
        executor: RemediationExecutorPort,
        policy: RemediationExecutionPolicy,
        lease_seconds: int = 60,
        request_timeout_seconds: float = 10.0,
        clock: Clock | None = None,
    ) -> None:
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 5 <= lease_seconds <= 3600
        ):
            raise ConflictError("Remediation lease_seconds is invalid")
        if (
            isinstance(request_timeout_seconds, bool)
            or not isinstance(request_timeout_seconds, (int, float))
            or not 0 < float(request_timeout_seconds) <= 60
        ):
            raise ConflictError("Remediation request_timeout_seconds is invalid")
        if float(request_timeout_seconds) >= float(lease_seconds):
            raise ConflictError(
                "Remediation request timeout must be shorter than the lease"
            )
        self._unit_of_work_factory = unit_of_work_factory
        self._identifier_generator = identifier_generator
        self._action_catalog = action_catalog
        self._executor = executor
        self._policy = policy
        self._lease_seconds = lease_seconds
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create(
        self,
        command: CreateRemediationPlanCommand,
    ) -> RemediationPlanView:
        command = self._sanitize_create(command)
        definition = self._action_catalog.get(command.action_key)
        definition.require_target(command.target)
        idempotency_hash = _hash(command.idempotency_key)
        request_hash = _create_request_hash(command)
        existing = await self._get_by_create_hash(
            command.tenant_id,
            idempotency_hash,
        )
        if existing is not None:
            return self._existing_create(existing, request_hash)

        plan_id = self._identifier_generator.new_remediation_plan_id()
        async with self._unit_of_work_factory() as unit_of_work:
            workflow = await unit_of_work.workflow_runs.get_by_id(
                command.tenant_id,
                command.workflow_run_id,
            )
            if workflow is None:
                raise ResourceNotFound("Workflow run not found")
            if workflow.status is not WorkflowRunStatus.SUCCEEDED:
                raise ConflictError("Remediation requires a succeeded workflow")
            if workflow.audit_purged_at is not None:
                raise ConflictError("Remediation requires available audit evidence")
            report = await unit_of_work.rca_reports.get_by_workflow_run(
                command.tenant_id,
                command.workflow_run_id,
            )
            if report is None:
                raise ConflictError("Remediation requires an RCA report")
            evidence = await unit_of_work.evidence.list_by_workflow_run(
                command.tenant_id,
                command.workflow_run_id,
                limit=100,
            )
        self._validate_evidence(command.evidence_ids, report.evidence_ids, evidence)

        controller_request = RemediationExecutionRequest(
            remediation_plan_id=plan_id,
            tenant_id=command.tenant_id,
            incident_id=report.incident_id,
            workflow_run_id=command.workflow_run_id,
            action_key=command.action_key,
            rollback_action_key=definition.rollback_action_key,
            target=command.target,
            idempotency_key=f"dry-run:{plan_id}",
            trace_id=command.trace_id,
        )
        outcome = await self._call_executor(
            "dry_run",
            controller_request,
        )
        if not outcome.succeeded:
            raise ConflictError("Remediation dry run was rejected")
        now = self._now()
        plan = RemediationPlan(
            remediation_plan_id=plan_id,
            tenant_id=command.tenant_id,
            workflow_run_id=command.workflow_run_id,
            incident_id=report.incident_id,
            action_key=command.action_key,
            target=command.target,
            expected_effect=definition.expected_effect,
            risk=RemediationRisk(definition.risk_level.value),
            rollback_action_key=definition.rollback_action_key,
            evidence_ids=command.evidence_ids,
            dry_run_summary=_safe_summary(outcome.summary),
            created_by=command.requested_by,
            created_at=now,
            trace_id=command.trace_id,
            create_idempotency_key_hash=idempotency_hash,
            create_request_hash=request_hash,
        )
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.remediation_plans.save(plan)
                await unit_of_work.outbox.add(
                    self._event(plan, "remediation.plan.created", now)
                )
                await unit_of_work.commit()
        except ConflictError:
            recovered = await self._get_by_create_hash(
                command.tenant_id,
                idempotency_hash,
            )
            if recovered is not None:
                return self._existing_create(recovered, request_hash)
            raise
        return RemediationPlanView.from_domain(plan)

    async def get(
        self,
        query: GetRemediationPlanQuery,
    ) -> RemediationPlanView:
        return RemediationPlanView.from_domain(
            await self._require_plan(
                query.tenant_id,
                query.remediation_plan_id,
            )
        )

    async def decide(
        self,
        command: DecideRemediationPlanCommand,
    ) -> RemediationPlanView:
        plan = await self._require_plan(
            command.tenant_id,
            command.remediation_plan_id,
        )
        idempotency_hash = _hash(command.idempotency_key)
        if plan.decision_idempotency_key_hash == idempotency_hash:
            expected_status = (
                RemediationStatus.APPROVED
                if command.approved
                else RemediationStatus.REJECTED
            )
            if (
                plan.status is not expected_status
                or plan.decision_reason != _safe_summary(command.reason)
                or plan.decided_by != command.requested_by
            ):
                raise ConflictError(
                    "Idempotency key was used for another decision"
                )
            return RemediationPlanView.from_domain(plan, is_duplicate=True)
        self._require_version(plan, command.expected_version)
        if command.approved and plan.created_by == command.requested_by:
            raise ConflictError(
                "Remediation author cannot approve their own plan"
            )
        now = self._now()
        updated = plan.decide(
            approved=command.approved,
            requested_by=command.requested_by,
            reason=_safe_summary(command.reason),
            decided_at=now,
            trace_id=command.trace_id,
            idempotency_key=command.idempotency_key,
        )
        await self._replace_with_event(
            updated,
            plan.version,
            "remediation.plan.decided",
            now,
            command.trace_id,
        )
        return RemediationPlanView.from_domain(updated)

    async def execute(
        self,
        command: ExecuteRemediationPlanCommand,
    ) -> RemediationPlanView:
        plan = await self._require_plan(
            command.tenant_id,
            command.remediation_plan_id,
        )
        idempotency_hash = _hash(command.idempotency_key)
        replaying = plan.execution_idempotency_key_hash == idempotency_hash
        if replaying and plan.status in {
            RemediationStatus.SUCCEEDED,
            RemediationStatus.FAILED,
        }:
            return RemediationPlanView.from_domain(plan, is_duplicate=True)
        if replaying and plan.executed_by != command.requested_by:
            raise ConflictError("Idempotency key was used by another operator")
        if not replaying:
            self._require_version(plan, command.expected_version)
            self._require_current_definition(plan)
            await self._require_available_audit_evidence(plan)
            now = self._now()
            self._policy.authorize_execution(
                tenant_id=plan.tenant_id,
                now=now,
            )
            started = plan.start_execution(
                requested_by=command.requested_by,
                started_at=now,
                lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                trace_id=command.trace_id,
                idempotency_key=command.idempotency_key,
            )
            await self._replace_with_event(
                started,
                plan.version,
                "remediation.execution.started",
                now,
                command.trace_id,
            )
            plan = started
        elif plan.status is not RemediationStatus.EXECUTING:
            raise ConflictError("Remediation plan cannot be executed")
        else:
            now = self._now()
            if not plan.execution_lease_active(now):
                failed = await self._fail_stale_execution(plan, now)
                raise ConflictError(
                    "Remediation execution lease expired; plan is "
                    f"{failed.status.value}"
                )

        claimed_attempt = plan.execution_attempt
        claimed_owner = plan.executed_by or command.requested_by
        try:
            outcome = await self._call_executor(
                "execute",
                self._execution_request(
                    plan,
                    f"execute:{plan.remediation_plan_id}",
                    command.trace_id,
                ),
            )
        except Exception as exc:
            await self._maybe_fail_expired_execution(
                plan.tenant_id,
                plan.remediation_plan_id,
            )
            raise ConflictError(
                f"Remediation executor failed during execute: {exc}"
            ) from exc

        completed_at = self._now()
        current = await self._require_plan(
            plan.tenant_id,
            plan.remediation_plan_id,
        )
        if current.status is not RemediationStatus.EXECUTING:
            if (
                current.execution_idempotency_key_hash == idempotency_hash
                and current.status
                in {RemediationStatus.SUCCEEDED, RemediationStatus.FAILED}
            ):
                return RemediationPlanView.from_domain(
                    current,
                    is_duplicate=True,
                )
            raise ConflictError("Remediation plan is not executing")
        if not current.execution_lease_active(completed_at):
            failed = await self._fail_stale_execution(current, completed_at)
            raise ConflictError(
                "Remediation execution lease expired; plan is "
                f"{failed.status.value}"
            )
        completed = current.finish_execution(
            succeeded=outcome.succeeded,
            summary=_safe_summary(outcome.summary),
            completed_at=completed_at,
            expected_attempt=claimed_attempt,
            requested_by=claimed_owner,
        )
        await self._replace_with_event(
            completed,
            current.version,
            "remediation.execution.completed",
            completed_at,
            command.trace_id,
        )
        return RemediationPlanView.from_domain(completed)

    async def rollback(
        self,
        command: RollbackRemediationPlanCommand,
    ) -> RemediationPlanView:
        plan = await self._require_plan(
            command.tenant_id,
            command.remediation_plan_id,
        )
        idempotency_hash = _hash(command.idempotency_key)
        replaying = plan.rollback_idempotency_key_hash == idempotency_hash
        if replaying and plan.status in {
            RemediationStatus.ROLLED_BACK,
            RemediationStatus.ROLLBACK_FAILED,
        }:
            return RemediationPlanView.from_domain(plan, is_duplicate=True)
        if replaying and plan.rolled_back_by != command.requested_by:
            raise ConflictError("Idempotency key was used by another operator")
        if not replaying:
            self._require_version(plan, command.expected_version)
            now = self._now()
            self._policy.authorize_execution(
                tenant_id=plan.tenant_id,
                now=now,
            )
            started = plan.start_rollback(
                requested_by=command.requested_by,
                started_at=now,
                lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                trace_id=command.trace_id,
                idempotency_key=command.idempotency_key,
            )
            await self._replace_with_event(
                started,
                plan.version,
                "remediation.rollback.started",
                now,
                command.trace_id,
            )
            plan = started
        elif plan.status is not RemediationStatus.ROLLING_BACK:
            raise ConflictError("Remediation plan cannot be rolled back")
        else:
            now = self._now()
            if not plan.rollback_lease_active(now):
                failed = await self._fail_stale_rollback(plan, now)
                raise ConflictError(
                    "Remediation rollback lease expired; plan is "
                    f"{failed.status.value}"
                )

        claimed_attempt = plan.rollback_attempt
        claimed_owner = plan.rolled_back_by or command.requested_by
        try:
            outcome = await self._call_executor(
                "rollback",
                self._execution_request(
                    plan,
                    f"rollback:{plan.remediation_plan_id}",
                    command.trace_id,
                ),
            )
        except Exception as exc:
            await self._maybe_fail_expired_rollback(
                plan.tenant_id,
                plan.remediation_plan_id,
            )
            raise ConflictError(
                f"Remediation executor failed during rollback: {exc}"
            ) from exc

        completed_at = self._now()
        current = await self._require_plan(
            plan.tenant_id,
            plan.remediation_plan_id,
        )
        if current.status is not RemediationStatus.ROLLING_BACK:
            if (
                current.rollback_idempotency_key_hash == idempotency_hash
                and current.status
                in {
                    RemediationStatus.ROLLED_BACK,
                    RemediationStatus.ROLLBACK_FAILED,
                }
            ):
                return RemediationPlanView.from_domain(
                    current,
                    is_duplicate=True,
                )
            raise ConflictError("Remediation plan is not rolling back")
        if not current.rollback_lease_active(completed_at):
            failed = await self._fail_stale_rollback(current, completed_at)
            raise ConflictError(
                "Remediation rollback lease expired; plan is "
                f"{failed.status.value}"
            )
        completed = current.finish_rollback(
            succeeded=outcome.succeeded,
            summary=_safe_summary(outcome.summary),
            completed_at=completed_at,
            expected_attempt=claimed_attempt,
            requested_by=claimed_owner,
        )
        await self._replace_with_event(
            completed,
            current.version,
            "remediation.rollback.completed",
            completed_at,
            command.trace_id,
        )
        return RemediationPlanView.from_domain(completed)

    async def reclaim_stale(
        self,
        *,
        limit: int = 50,
    ) -> list[RemediationPlanView]:
        """扫描并收口过期执行/回滚租约。"""
        now = self._now()
        reclaimed: list[RemediationPlanView] = []
        async with self._unit_of_work_factory() as unit_of_work:
            stale_executions = (
                await unit_of_work.remediation_plans.list_stale_execution(
                    now=now,
                    limit=limit,
                )
            )
            stale_rollbacks = (
                await unit_of_work.remediation_plans.list_stale_rollback(
                    now=now,
                    limit=limit,
                )
            )
        for plan in stale_executions:
            try:
                failed = await self._fail_stale_execution(plan, now)
            except ConflictError:
                continue
            reclaimed.append(RemediationPlanView.from_domain(failed))
        for plan in stale_rollbacks:
            try:
                failed = await self._fail_stale_rollback(plan, now)
            except ConflictError:
                continue
            reclaimed.append(RemediationPlanView.from_domain(failed))
        return reclaimed

    async def _fail_stale_execution(
        self,
        plan: RemediationPlan,
        now: datetime,
    ) -> RemediationPlan:
        failed = plan.fail_stale_execution(
            now=now,
            summary=_STALE_EXECUTION_SUMMARY,
        )
        await self._replace_with_event(
            failed,
            plan.version,
            "remediation.execution.lease_expired",
            now,
            plan.execution_trace_id or plan.trace_id,
        )
        return failed

    async def _fail_stale_rollback(
        self,
        plan: RemediationPlan,
        now: datetime,
    ) -> RemediationPlan:
        failed = plan.fail_stale_rollback(
            now=now,
            summary=_STALE_ROLLBACK_SUMMARY,
        )
        await self._replace_with_event(
            failed,
            plan.version,
            "remediation.rollback.lease_expired",
            now,
            plan.rollback_trace_id or plan.trace_id,
        )
        return failed

    async def _maybe_fail_expired_execution(
        self,
        tenant_id: str,
        remediation_plan_id: str,
    ) -> None:
        try:
            plan = await self._require_plan(tenant_id, remediation_plan_id)
        except ResourceNotFound:
            return
        now = self._now()
        if (
            plan.status is RemediationStatus.EXECUTING
            and not plan.execution_lease_active(now)
        ):
            try:
                await self._fail_stale_execution(plan, now)
            except ConflictError:
                return

    async def _maybe_fail_expired_rollback(
        self,
        tenant_id: str,
        remediation_plan_id: str,
    ) -> None:
        try:
            plan = await self._require_plan(tenant_id, remediation_plan_id)
        except ResourceNotFound:
            return
        now = self._now()
        if (
            plan.status is RemediationStatus.ROLLING_BACK
            and not plan.rollback_lease_active(now)
        ):
            try:
                await self._fail_stale_rollback(plan, now)
            except ConflictError:
                return

    async def _call_executor(
        self,
        operation: str,
        request: RemediationExecutionRequest,
    ):
        method = getattr(self._executor, operation)
        try:
            return await asyncio.wait_for(
                method(request),
                timeout=self._request_timeout_seconds,
            )
        except TimeoutError as exc:
            raise ConflictError(
                f"Remediation executor timed out during {operation}"
            ) from exc

    async def _replace_with_event(
        self,
        plan: RemediationPlan,
        expected_version: int,
        event_type: str,
        occurred_at: datetime,
        trace_id: str | None = None,
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.remediation_plans.replace(plan, expected_version)
            await unit_of_work.outbox.add(
                self._event(
                    plan,
                    event_type,
                    occurred_at,
                    trace_id=trace_id,
                )
            )
            await unit_of_work.commit()

    async def _require_plan(
        self,
        tenant_id: str,
        remediation_plan_id: str,
    ) -> RemediationPlan:
        async with self._unit_of_work_factory() as unit_of_work:
            plan = await unit_of_work.remediation_plans.get_by_id(
                tenant_id,
                remediation_plan_id,
            )
        if plan is None:
            raise ResourceNotFound("Remediation plan not found")
        return plan

    async def _get_by_create_hash(
        self,
        tenant_id: str,
        idempotency_hash: str,
    ) -> RemediationPlan | None:
        async with self._unit_of_work_factory() as unit_of_work:
            return (
                await unit_of_work.remediation_plans
                .get_by_create_idempotency_key_hash(
                    tenant_id,
                    idempotency_hash,
                )
            )

    async def _require_available_audit_evidence(
        self,
        plan: RemediationPlan,
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            workflow = await unit_of_work.workflow_runs.get_by_id(
                plan.tenant_id,
                plan.workflow_run_id,
            )
        if workflow is None or workflow.audit_purged_at is not None:
            raise ConflictError("Remediation requires available audit evidence")

    def _require_current_definition(self, plan: RemediationPlan) -> None:
        definition = self._action_catalog.get(plan.action_key)
        definition.require_target(plan.target)
        if (
            definition.rollback_action_key != plan.rollback_action_key
            or definition.expected_effect != plan.expected_effect
            or definition.risk_level.value != plan.risk.value
        ):
            raise ConflictError("Remediation action definition has changed")

    @staticmethod
    def _validate_evidence(
        requested_ids: tuple[str, ...],
        report_ids: tuple[str, ...],
        evidence: list,
    ) -> None:
        if not set(requested_ids).issubset(report_ids):
            raise ConflictError("Remediation evidence must be cited by the report")
        selected = {
            item.evidence_id: item
            for item in evidence
            if item.evidence_id in requested_ids
        }
        if set(selected) != set(requested_ids):
            raise ConflictError("Remediation evidence is unavailable")
        actual_types = {item.evidence_type for item in selected.values()}
        if not _REQUIRED_EVIDENCE_TYPES.issubset(actual_types):
            raise ConflictError(
                "Remediation requires metric, log, trace and runbook evidence"
            )

    @staticmethod
    def _require_version(plan: RemediationPlan, expected_version: int) -> None:
        if plan.version != expected_version:
            raise ConflictError("Remediation plan version conflict")

    @staticmethod
    def _existing_create(
        plan: RemediationPlan,
        request_hash: str,
    ) -> RemediationPlanView:
        if plan.create_request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was used for another remediation plan"
            )
        return RemediationPlanView.from_domain(plan, is_duplicate=True)

    @staticmethod
    def _execution_request(
        plan: RemediationPlan,
        idempotency_key: str,
        trace_id: str,
    ) -> RemediationExecutionRequest:
        return RemediationExecutionRequest(
            remediation_plan_id=plan.remediation_plan_id,
            tenant_id=plan.tenant_id,
            incident_id=plan.incident_id,
            workflow_run_id=plan.workflow_run_id,
            action_key=plan.action_key,
            rollback_action_key=plan.rollback_action_key,
            target=plan.target,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )

    def _event(
        self,
        plan: RemediationPlan,
        event_type: str,
        occurred_at: datetime,
        *,
        trace_id: str | None = None,
    ) -> OutboxEvent:
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=plan.tenant_id,
            aggregate_type="RemediationPlan",
            aggregate_id=plan.remediation_plan_id,
            event_type=event_type,
            schema_version=1,
            payload={
                "remediation_plan_id": plan.remediation_plan_id,
                "workflow_run_id": plan.workflow_run_id,
                "incident_id": plan.incident_id,
                "action_key": plan.action_key,
                "rollback_action_key": plan.rollback_action_key,
                "risk": plan.risk.value,
                "status": plan.status.value,
                "version": plan.version,
                "execution_attempt": plan.execution_attempt,
                "rollback_attempt": plan.rollback_attempt,
            },
            occurred_at=occurred_at,
            trace_id=trace_id or plan.trace_id,
        )

    @staticmethod
    def _sanitize_create(
        command: CreateRemediationPlanCommand,
    ) -> CreateRemediationPlanCommand:
        target = " ".join(command.target.split()).strip()
        target = redact_sensitive_text(target)[0][:256].strip()
        return replace(
            command,
            target=target,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ConflictError("Remediation clock is invalid")
        return value.astimezone(UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _create_request_hash(command: CreateRemediationPlanCommand) -> str:
    document = {
        "tenant_id": command.tenant_id,
        "workflow_run_id": command.workflow_run_id,
        "action_key": command.action_key,
        "target": command.target,
        "evidence_ids": list(command.evidence_ids),
        "requested_by": command.requested_by,
    }
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_summary(value: str) -> str:
    normalized = "\n".join(line.strip() for line in value.splitlines()).strip()
    return redact_sensitive_text(normalized)[0][:4096].strip()
