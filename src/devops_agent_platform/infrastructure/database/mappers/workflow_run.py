from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.database.models.workflow_run import (
    WorkflowRunRecord,
)


class WorkflowRunMapper:
    """在WorkflowRun领域对象和数据库记录之间转换。"""

    @staticmethod
    def to_record(workflow_run: WorkflowRun) -> WorkflowRunRecord:
        """构造新的数据库记录。"""
        return WorkflowRunRecord(
            workflow_run_id=workflow_run.workflow_run_id,
            tenant_id=workflow_run.tenant_id,
            incident_id=workflow_run.incident_id,
            operator_id=workflow_run.operator_id,
            idempotency_key_hash=workflow_run.idempotency_key_hash,
            request_hash=workflow_run.request_hash,
            trace_id=workflow_run.trace_id,
            status=workflow_run.status.value,
            created_at=workflow_run.created_at,
            updated_at=workflow_run.updated_at,
            started_at=workflow_run.started_at,
            ended_at=workflow_run.ended_at,
            audit_purged_at=workflow_run.audit_purged_at,
            canceled_by=workflow_run.canceled_by,
            cancellation_reason=workflow_run.cancellation_reason,
            canceled_at=workflow_run.canceled_at,
            cancellation_idempotency_key_hash=(
                workflow_run.cancellation_idempotency_key_hash
            ),
            cancellation_request_hash=workflow_run.cancellation_request_hash,
            cancellation_trace_id=workflow_run.cancellation_trace_id,
            step_count=workflow_run.step_count,
            lease_owner=workflow_run.lease_owner,
            lease_expires_at=workflow_run.lease_expires_at,
            heartbeat_at=workflow_run.heartbeat_at,
            execution_attempts=workflow_run.execution_attempts,
            version=workflow_run.version,
        )

    @staticmethod
    def to_domain(record: WorkflowRunRecord) -> WorkflowRun:
        """把数据库记录恢复为领域对象。"""
        return WorkflowRun(
            workflow_run_id=record.workflow_run_id,
            tenant_id=record.tenant_id,
            incident_id=record.incident_id,
            operator_id=record.operator_id,
            idempotency_key_hash=record.idempotency_key_hash,
            request_hash=record.request_hash,
            trace_id=record.trace_id,
            status=WorkflowRunStatus(record.status),
            created_at=record.created_at,
            updated_at=record.updated_at,
            started_at=record.started_at,
            ended_at=record.ended_at,
            audit_purged_at=record.audit_purged_at,
            canceled_by=record.canceled_by,
            cancellation_reason=record.cancellation_reason,
            canceled_at=record.canceled_at,
            cancellation_idempotency_key_hash=(
                record.cancellation_idempotency_key_hash
            ),
            cancellation_request_hash=record.cancellation_request_hash,
            cancellation_trace_id=record.cancellation_trace_id,
            step_count=record.step_count,
            lease_owner=record.lease_owner,
            lease_expires_at=record.lease_expires_at,
            heartbeat_at=record.heartbeat_at,
            execution_attempts=record.execution_attempts,
            version=record.version,
        )
