from devops_agent_platform.domain.enums import TicketSubmissionStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.ticket_submission import TicketSubmission
from devops_agent_platform.infrastructure.database.models.ticket_submission import (
    TicketSubmissionRecord,
)


class TicketSubmissionMapper:
    """在 TicketSubmission 领域快照与 ORM 记录之间转换。"""

    @staticmethod
    def to_record(submission: TicketSubmission) -> TicketSubmissionRecord:
        """把提交请求映射为数据库记录。"""
        return TicketSubmissionRecord(
            ticket_submission_id=submission.ticket_submission_id,
            tenant_id=submission.tenant_id,
            ticket_draft_id=submission.ticket_draft_id,
            workflow_run_id=submission.workflow_run_id,
            target_system=submission.target_system,
            status=submission.status.value,
            idempotency_key_hash=submission.idempotency_key_hash,
            request_hash=submission.request_hash,
            requested_by=submission.requested_by,
            trace_id=submission.trace_id,
            requested_at=submission.requested_at,
            version=submission.version,
            external_ticket_id=submission.external_ticket_id,
            external_ticket_url=submission.external_ticket_url,
            failure_reason=submission.failure_reason,
            completed_by=submission.completed_by,
            completed_at=submission.completed_at,
            result_idempotency_key_hash=(
                submission.result_idempotency_key_hash
            ),
            result_request_hash=submission.result_request_hash,
            result_trace_id=submission.result_trace_id,
        )

    @staticmethod
    def to_domain(record: TicketSubmissionRecord) -> TicketSubmission:
        """恢复领域对象并重新验证数据库内容。"""
        try:
            status = TicketSubmissionStatus(record.status)
        except ValueError as exc:
            raise AppValidationError(
                "ticket submission status is invalid"
            ) from exc
        return TicketSubmission(
            ticket_submission_id=record.ticket_submission_id,
            tenant_id=record.tenant_id,
            ticket_draft_id=record.ticket_draft_id,
            workflow_run_id=record.workflow_run_id,
            target_system=record.target_system,
            status=status,
            idempotency_key_hash=record.idempotency_key_hash,
            request_hash=record.request_hash,
            requested_by=record.requested_by,
            trace_id=record.trace_id,
            requested_at=record.requested_at,
            version=record.version,
            external_ticket_id=record.external_ticket_id,
            external_ticket_url=record.external_ticket_url,
            failure_reason=record.failure_reason,
            completed_by=record.completed_by,
            completed_at=record.completed_at,
            result_idempotency_key_hash=(
                record.result_idempotency_key_hash
            ),
            result_request_hash=record.result_request_hash,
            result_trace_id=record.result_trace_id,
        )
