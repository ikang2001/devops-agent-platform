import json
from dataclasses import fields

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.remediation import (
    RemediationPlan,
    RemediationRisk,
    RemediationStatus,
)
from devops_agent_platform.infrastructure.database.models.remediation import (
    RemediationPlanRecord,
)


class RemediationPlanMapper:
    """在受约束领域状态机和数据库记录之间严格映射。"""

    @staticmethod
    def to_record(plan: RemediationPlan) -> RemediationPlanRecord:
        record = RemediationPlanRecord()
        RemediationPlanMapper.apply(plan, record)
        return record

    @staticmethod
    def apply(
        plan: RemediationPlan,
        record: RemediationPlanRecord,
    ) -> None:
        for field in fields(RemediationPlan):
            name = field.name
            value = getattr(plan, name)
            if name == "risk":
                value = value.value
            elif name == "status":
                value = value.value
            elif name == "evidence_ids":
                name = "evidence_ids_json"
                value = json.dumps(
                    list(value),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            setattr(record, name, value)

    @staticmethod
    def to_domain(record: RemediationPlanRecord) -> RemediationPlan:
        try:
            evidence_ids = json.loads(record.evidence_ids_json)
            if not isinstance(evidence_ids, list) or not all(
                isinstance(item, str) for item in evidence_ids
            ):
                raise AppValidationError(
                    "stored remediation evidence_ids are invalid"
                )
            return RemediationPlan(
                remediation_plan_id=record.remediation_plan_id,
                tenant_id=record.tenant_id,
                workflow_run_id=record.workflow_run_id,
                incident_id=record.incident_id,
                action_key=record.action_key,
                target=record.target,
                expected_effect=record.expected_effect,
                risk=RemediationRisk(record.risk),
                rollback_action_key=record.rollback_action_key,
                evidence_ids=tuple(evidence_ids),
                dry_run_summary=record.dry_run_summary,
                created_by=record.created_by,
                created_at=record.created_at,
                trace_id=record.trace_id,
                create_idempotency_key_hash=(
                    record.create_idempotency_key_hash
                ),
                create_request_hash=record.create_request_hash,
                status=RemediationStatus(record.status),
                version=record.version,
                decided_by=record.decided_by,
                decision_reason=record.decision_reason,
                decided_at=record.decided_at,
                decision_trace_id=record.decision_trace_id,
                decision_idempotency_key_hash=(
                    record.decision_idempotency_key_hash
                ),
                execution_summary=record.execution_summary,
                execution_started_at=record.execution_started_at,
                executed_at=record.executed_at,
                executed_by=record.executed_by,
                execution_trace_id=record.execution_trace_id,
                execution_idempotency_key_hash=(
                    record.execution_idempotency_key_hash
                ),
                execution_attempt=record.execution_attempt,
                execution_lease_expires_at=record.execution_lease_expires_at,
                rollback_summary=record.rollback_summary,
                rollback_started_at=record.rollback_started_at,
                rolled_back_at=record.rolled_back_at,
                rolled_back_by=record.rolled_back_by,
                rollback_trace_id=record.rollback_trace_id,
                rollback_idempotency_key_hash=(
                    record.rollback_idempotency_key_hash
                ),
                rollback_attempt=record.rollback_attempt,
                rollback_lease_expires_at=record.rollback_lease_expires_at,
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise AppValidationError(
                "stored remediation plan is invalid"
            ) from exc
