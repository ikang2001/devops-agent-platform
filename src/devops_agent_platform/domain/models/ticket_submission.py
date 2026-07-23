import re
from dataclasses import dataclass, replace
from datetime import datetime

from devops_agent_platform.domain.enums import TicketSubmissionStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id

_TARGET_SYSTEM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


@dataclass(frozen=True)
class TicketSubmission:
    """已审批 Ticket Draft 发起外部提交的本地不可变请求记录。

    这个对象只表达“平台已经请求把草稿提交到某个外部工单系统”，不代表外部
    Jira、ServiceNow 等系统已经建单成功。真实外部结果后续应由独立 Worker
    回填，避免 HTTP 请求线程承担慢外部依赖。
    """

    ticket_submission_id: str
    tenant_id: str
    ticket_draft_id: str
    workflow_run_id: str
    target_system: str
    status: TicketSubmissionStatus
    idempotency_key_hash: str
    request_hash: str
    requested_by: str
    trace_id: str
    requested_at: datetime
    version: int = 1
    external_ticket_id: str | None = None
    external_ticket_url: str | None = None
    failure_reason: str | None = None
    completed_by: str | None = None
    completed_at: datetime | None = None
    result_idempotency_key_hash: str | None = None
    result_request_hash: str | None = None
    result_trace_id: str | None = None

    def __post_init__(self) -> None:
        """校验租户边界、目标系统、幂等摘要和请求时间。"""
        for field_name, value, maximum in (
            ("ticket_submission_id", self.ticket_submission_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("ticket_draft_id", self.ticket_draft_id, 64),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            self._validate_text(field_name, value, maximum)
        self._validate_target_system(self.target_system)
        self._validate_hash("idempotency_key_hash", self.idempotency_key_hash)
        self._validate_hash("request_hash", self.request_hash)
        if (
            not isinstance(self.requested_at, datetime)
            or self.requested_at.tzinfo is None
            or self.requested_at.utcoffset() is None
        ):
            raise AppValidationError("requested_at must include timezone information")
        self._validate_result_state()

    def mark_submitted(
        self,
        *,
        external_ticket_id: str,
        external_ticket_url: str | None,
        completed_at: datetime,
        idempotency_key_hash: str,
        request_hash: str,
        trace_id: str,
        completed_by: str,
    ) -> "TicketSubmission":
        """把提交请求单向标记为外部系统已建单。"""
        self._ensure_requested()
        return replace(
            self,
            status=TicketSubmissionStatus.SUBMITTED,
            version=2,
            external_ticket_id=external_ticket_id,
            external_ticket_url=external_ticket_url,
            completed_by=completed_by,
            completed_at=completed_at,
            result_idempotency_key_hash=idempotency_key_hash,
            result_request_hash=request_hash,
            result_trace_id=trace_id,
        )

    def mark_failed(
        self,
        *,
        failure_reason: str,
        completed_at: datetime,
        idempotency_key_hash: str,
        request_hash: str,
        trace_id: str,
        completed_by: str,
    ) -> "TicketSubmission":
        """把提交请求单向标记为外部提交失败。"""
        self._ensure_requested()
        return replace(
            self,
            status=TicketSubmissionStatus.FAILED,
            version=2,
            failure_reason=failure_reason,
            completed_by=completed_by,
            completed_at=completed_at,
            result_idempotency_key_hash=idempotency_key_hash,
            result_request_hash=request_hash,
            result_trace_id=trace_id,
        )

    def _ensure_requested(self) -> None:
        """只允许未完成请求进入终态。"""
        if self.status is not TicketSubmissionStatus.REQUESTED:
            raise AppValidationError(
                "only requested ticket submissions can be completed"
            )

    def _validate_result_state(self) -> None:
        """保证提交状态、版本和结果字段同步变化。"""
        if not isinstance(self.status, TicketSubmissionStatus):
            raise AppValidationError("status must be a TicketSubmissionStatus")
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version not in {1, 2}
        ):
            raise AppValidationError("ticket submission version must be 1 or 2")
        result_fields = (
            self.external_ticket_id,
            self.external_ticket_url,
            self.failure_reason,
            self.completed_by,
            self.completed_at,
            self.result_idempotency_key_hash,
            self.result_request_hash,
            self.result_trace_id,
        )
        if self.status is TicketSubmissionStatus.REQUESTED:
            if self.version != 1 or any(value is not None for value in result_fields):
                raise AppValidationError(
                    "requested ticket submission must not contain result fields"
                )
            return
        if (
            self.version != 2
            or self.completed_at is None
            or self.completed_by is None
            or self.result_idempotency_key_hash is None
            or self.result_request_hash is None
            or self.result_trace_id is None
        ):
            raise AppValidationError(
                "completed ticket submission requires result metadata"
            )
        validate_worker_id(self.completed_by)
        if (
            self.completed_at.tzinfo is None
            or self.completed_at.utcoffset() is None
            or self.completed_at < self.requested_at
        ):
            raise AppValidationError("completed_at is invalid")
        self._validate_hash(
            "result_idempotency_key_hash",
            self.result_idempotency_key_hash,
        )
        self._validate_hash("result_request_hash", self.result_request_hash)
        self._validate_text("result_trace_id", self.result_trace_id, 128)
        if self.status is TicketSubmissionStatus.SUBMITTED:
            self._validate_text(
                "external_ticket_id",
                self.external_ticket_id or "",
                256,
            )
            if self.external_ticket_url is not None:
                self._validate_text(
                    "external_ticket_url",
                    self.external_ticket_url,
                    2048,
                )
            if self.failure_reason is not None:
                raise AppValidationError(
                    "submitted ticket submission must not contain failure reason"
                )
        elif self.status is TicketSubmissionStatus.FAILED:
            self._validate_text(
                "failure_reason",
                self.failure_reason or "",
                2048,
            )
            if (
                self.external_ticket_id is not None
                or self.external_ticket_url is not None
            ):
                raise AppValidationError(
                    "failed ticket submission must not contain external ticket"
                )

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        """拒绝空白、超长和控制字符，避免污染索引与审计日志。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")

    @staticmethod
    def _validate_target_system(value: str) -> None:
        """目标系统使用规范化短标识，便于后续按适配器路由。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 64
            or _TARGET_SYSTEM_PATTERN.fullmatch(value) is None
        ):
            raise AppValidationError("target_system is invalid")

    @staticmethod
    def _validate_hash(field_name: str, value: str) -> None:
        """只接受 SHA-256 小写十六进制摘要。"""
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise AppValidationError(f"{field_name} must be a SHA-256 hex digest")
