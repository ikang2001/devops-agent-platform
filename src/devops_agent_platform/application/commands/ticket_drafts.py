from dataclasses import dataclass

from devops_agent_platform.domain.enums import (
    TicketDecision,
    TicketSubmissionStatus,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id


@dataclass(frozen=True)
class CreateTicketDraftCommand:
    """从一个已成功 RCA 工作流创建本地工单草稿。"""

    tenant_id: str
    workflow_run_id: str
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验租户、来源工作流、幂等键和审计身份。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("idempotency_key", self.idempotency_key, 128),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    character.isspace() or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{field_name} is invalid")


@dataclass(frozen=True)
class DecideTicketDraftCommand:
    """对一个明确版本的本地工单草稿作人工确认。"""

    tenant_id: str
    workflow_run_id: str
    decision: TicketDecision
    reason: str | None
    expected_version: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验审批身份、条件版本、幂等键和拒绝原因。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("idempotency_key", self.idempotency_key, 128),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    character.isspace() or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{field_name} is invalid")
        if not isinstance(self.decision, TicketDecision):
            raise AppValidationError("decision must be a TicketDecision")
        if (
            isinstance(self.expected_version, bool)
            or not isinstance(self.expected_version, int)
            or self.expected_version < 1
        ):
            raise AppValidationError("expected_version must be a positive integer")
        if self.decision is TicketDecision.APPROVE:
            if self.reason is not None:
                raise AppValidationError("approval must not contain a rejection reason")
        elif (
            not isinstance(self.reason, str)
            or not 1 <= len(self.reason) <= 2048
            or self.reason != self.reason.strip()
            or any(
                (ord(character) < 32 and character != "\n") or ord(character) == 127
                for character in self.reason
            )
        ):
            raise AppValidationError("rejection reason is invalid")


@dataclass(frozen=True)
class SubmitTicketDraftCommand:
    """把已审批 Ticket Draft 提交到指定外部工单系统的请求。"""

    tenant_id: str
    workflow_run_id: str
    target_system: str
    expected_draft_version: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验目标系统、条件版本、幂等键和可信提交人。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("target_system", self.target_system, 64),
            ("idempotency_key", self.idempotency_key, 128),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    character.isspace() or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{field_name} is invalid")
        if self.target_system.lower() != self.target_system:
            raise AppValidationError("target_system must be lowercase")
        if (
            isinstance(self.expected_draft_version, bool)
            or not isinstance(self.expected_draft_version, int)
            or self.expected_draft_version < 1
        ):
            raise AppValidationError(
                "expected_draft_version must be a positive integer"
            )


@dataclass(frozen=True)
class CompleteTicketSubmissionCommand:
    """记录外部工单提交请求的最终结果。"""

    tenant_id: str
    ticket_submission_id: str
    result_status: TicketSubmissionStatus
    expected_version: int
    idempotency_key: str
    completed_by: str
    trace_id: str
    external_ticket_id: str | None = None
    external_ticket_url: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        """校验 worker 身份、终态结果、幂等键和结果内容。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("ticket_submission_id", self.ticket_submission_id, 64),
            ("idempotency_key", self.idempotency_key, 128),
            ("trace_id", self.trace_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    character.isspace() or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{field_name} is invalid")
        validate_worker_id(self.completed_by)
        if self.result_status not in {
            TicketSubmissionStatus.SUBMITTED,
            TicketSubmissionStatus.FAILED,
        }:
            raise AppValidationError("result_status must be SUBMITTED or FAILED")
        if (
            isinstance(self.expected_version, bool)
            or not isinstance(self.expected_version, int)
            or self.expected_version < 1
        ):
            raise AppValidationError("expected_version must be a positive integer")
        if self.result_status is TicketSubmissionStatus.SUBMITTED:
            self._validate_optional_result_text(
                "external_ticket_id",
                self.external_ticket_id,
                maximum=256,
                required=True,
            )
            self._validate_optional_result_text(
                "external_ticket_url",
                self.external_ticket_url,
                maximum=2048,
                required=False,
            )
            if self.failure_reason is not None:
                raise AppValidationError(
                    "submitted result must not contain failure_reason"
                )
        else:
            self._validate_optional_result_text(
                "failure_reason",
                self.failure_reason,
                maximum=2048,
                required=True,
            )
            if (
                self.external_ticket_id is not None
                or self.external_ticket_url is not None
            ):
                raise AppValidationError(
                    "failed result must not contain external ticket"
                )

    @staticmethod
    def _validate_optional_result_text(
        field_name: str,
        value: str | None,
        *,
        maximum: int,
        required: bool,
    ) -> None:
        """校验外部系统结果文本，允许 URL 等字段为空。"""
        if value is None:
            if required:
                raise AppValidationError(f"{field_name} is required")
            return
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")
