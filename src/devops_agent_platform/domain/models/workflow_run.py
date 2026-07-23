from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)


@dataclass
class WorkflowRun:
    """一次RCA工作流执行聚合，负责约束状态和时间线。"""

    workflow_run_id: str
    tenant_id: str
    incident_id: str
    operator_id: str
    idempotency_key_hash: str
    request_hash: str
    trace_id: str
    status: WorkflowRunStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    step_count: int
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    execution_attempts: int = 0
    version: int = 1
    audit_purged_at: datetime | None = None
    canceled_by: str | None = None
    cancellation_reason: str | None = None
    canceled_at: datetime | None = None
    cancellation_idempotency_key_hash: str | None = None
    cancellation_request_hash: str | None = None
    cancellation_trace_id: str | None = None

    def __post_init__(self) -> None:
        """校验身份、哈希、状态时间线和乐观锁版本。"""
        self._validate_text("workflow_run_id", self.workflow_run_id, 64)
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("incident_id", self.incident_id, 64)
        self._validate_text("operator_id", self.operator_id, 128)
        self._validate_hash("idempotency_key_hash", self.idempotency_key_hash)
        self._validate_hash("request_hash", self.request_hash)
        self._validate_text("trace_id", self.trace_id, 128)
        if not isinstance(self.status, WorkflowRunStatus):
            raise AppValidationError("status must be a WorkflowRunStatus")
        self._validate_timestamp("created_at", self.created_at)
        self._validate_timestamp("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise AppValidationError("updated_at must not be earlier than created_at")
        if self.started_at is not None:
            self._validate_timestamp("started_at", self.started_at)
            if self.started_at < self.created_at:
                raise AppValidationError(
                    "started_at must not be earlier than created_at"
                )
        if self.ended_at is not None:
            self._validate_timestamp("ended_at", self.ended_at)
            baseline = self.started_at or self.created_at
            if self.ended_at < baseline:
                raise AppValidationError(
                    "ended_at must not be earlier than workflow start"
                )
        if (
            isinstance(self.step_count, bool)
            or not isinstance(self.step_count, int)
            or self.step_count < 0
        ):
            raise AppValidationError("step_count must be a non-negative integer")
        if (
            isinstance(self.execution_attempts, bool)
            or not isinstance(self.execution_attempts, int)
            or self.execution_attempts < 0
        ):
            raise AppValidationError(
                "execution_attempts must be a non-negative integer"
            )
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < 1
        ):
            raise AppValidationError("version must be a positive integer")
        self._validate_execution_lease()
        self._validate_status_timeline()
        self._validate_audit_retention()
        self._validate_cancellation_state()

    def start(
        self,
        now: datetime,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> None:
        """将待执行任务推进到运行中，并绑定有期限的执行租约。"""
        self._validate_transition_time(now)
        if self.status is not WorkflowRunStatus.PENDING:
            raise ConflictError("Workflow run is not pending")
        self._validate_text("lease_owner", lease_owner, 128)
        self._validate_timestamp("lease_expires_at", lease_expires_at)
        if lease_expires_at <= now:
            raise AppValidationError("lease_expires_at must be later than now")
        self.status = WorkflowRunStatus.RUNNING
        self.started_at = now
        self.updated_at = now
        self.lease_owner = lease_owner
        self.lease_expires_at = lease_expires_at
        self.heartbeat_at = now
        self.execution_attempts += 1

    def record_step(self, now: datetime) -> None:
        """记录一个已完成步骤，只允许运行中任务调用。"""
        self._validate_transition_time(now)
        if self.status is not WorkflowRunStatus.RUNNING:
            raise ConflictError("Workflow run is not running")
        self.step_count += 1
        self.updated_at = now

    def heartbeat(
        self,
        now: datetime,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> None:
        """由当前所有者在旧租约有效期内续租。"""
        self._validate_transition_time(now)
        if self.status is not WorkflowRunStatus.RUNNING:
            raise ConflictError("Workflow run is not running")
        self._validate_text("lease_owner", lease_owner, 128)
        if self.lease_owner != lease_owner:
            raise ConflictError("Workflow execution lease owner changed")
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            raise ConflictError("Workflow execution lease expired")
        self._validate_timestamp("lease_expires_at", lease_expires_at)
        if lease_expires_at <= now:
            raise AppValidationError("lease_expires_at must be later than now")
        if (
            self.lease_expires_at is not None
            and lease_expires_at <= self.lease_expires_at
        ):
            raise AppValidationError(
                "heartbeat must extend the current execution lease"
            )
        self.heartbeat_at = now
        self.updated_at = now
        self.lease_expires_at = lease_expires_at

    def mark_succeeded(
        self,
        now: datetime,
        lease_owner: str,
        execution_attempt: int,
    ) -> None:
        """由当前有效执行代次将任务标记为成功。"""
        self._finish(
            WorkflowRunStatus.SUCCEEDED,
            now,
            lease_owner,
            execution_attempt,
        )

    def mark_failed(
        self,
        now: datetime,
        lease_owner: str,
        execution_attempt: int,
    ) -> None:
        """由当前有效执行代次将任务标记为失败。"""
        self._finish(
            WorkflowRunStatus.FAILED,
            now,
            lease_owner,
            execution_attempt,
        )

    def cancel(
        self,
        now: datetime,
        *,
        canceled_by: str | None = None,
        reason: str | None = None,
        idempotency_key_hash: str | None = None,
        request_hash: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """取消待执行或运行中的任务，可选写入控制面审计事实。"""
        self._validate_transition_time(now)
        if self.status not in {
            WorkflowRunStatus.PENDING,
            WorkflowRunStatus.RUNNING,
        }:
            raise ConflictError("Workflow run is already terminal")
        if any(
            value is not None
            for value in (
                canceled_by,
                reason,
                idempotency_key_hash,
                request_hash,
                trace_id,
            )
        ):
            if any(
                value is None
                for value in (
                    canceled_by,
                    reason,
                    idempotency_key_hash,
                    request_hash,
                    trace_id,
                )
            ):
                raise AppValidationError("cancellation metadata must be complete")
            assert canceled_by is not None
            assert reason is not None
            assert idempotency_key_hash is not None
            assert request_hash is not None
            assert trace_id is not None
            self._validate_text("canceled_by", canceled_by, 128)
            self._validate_text("cancellation_reason", reason, 2048)
            self._validate_hash(
                "cancellation_idempotency_key_hash",
                idempotency_key_hash,
            )
            self._validate_hash("cancellation_request_hash", request_hash)
            self._validate_text("cancellation_trace_id", trace_id, 128)
            self.canceled_by = canceled_by
            self.cancellation_reason = reason
            self.cancellation_idempotency_key_hash = idempotency_key_hash
            self.cancellation_request_hash = request_hash
            self.cancellation_trace_id = trace_id
        self.status = WorkflowRunStatus.CANCELED
        self.updated_at = now
        self.ended_at = now
        self.canceled_at = now if canceled_by is not None else None
        self._clear_execution_lease()

    def _finish(
        self,
        status: WorkflowRunStatus,
        now: datetime,
        lease_owner: str,
        execution_attempt: int,
    ) -> None:
        """校验租约和执行代次后完成任务。"""
        self._validate_transition_time(now)
        if self.status is not WorkflowRunStatus.RUNNING:
            raise ConflictError("Workflow run is not running")
        self._validate_text("lease_owner", lease_owner, 128)
        if self.lease_owner != lease_owner:
            raise ConflictError("Workflow execution lease owner changed")
        if (
            isinstance(execution_attempt, bool)
            or not isinstance(execution_attempt, int)
            or execution_attempt < 1
        ):
            raise AppValidationError("execution_attempt must be a positive integer")
        if self.execution_attempts != execution_attempt:
            raise ConflictError("Workflow execution attempt changed")
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            raise ConflictError("Workflow execution lease expired")
        self.status = status
        self.updated_at = now
        self.ended_at = now
        self._clear_execution_lease()

    def _validate_execution_lease(self) -> None:
        """保证运行状态与租约负责人、心跳和过期时间保持一致。"""
        lease_values = (
            self.lease_owner,
            self.lease_expires_at,
            self.heartbeat_at,
        )
        if self.status is WorkflowRunStatus.RUNNING:
            if any(value is None for value in lease_values):
                raise AppValidationError(
                    "running workflow must have a complete execution lease"
                )
            assert self.lease_owner is not None
            assert self.lease_expires_at is not None
            assert self.heartbeat_at is not None
            self._validate_text("lease_owner", self.lease_owner, 128)
            self._validate_timestamp(
                "lease_expires_at",
                self.lease_expires_at,
            )
            self._validate_timestamp("heartbeat_at", self.heartbeat_at)
            if self.started_at is not None and self.heartbeat_at < self.started_at:
                raise AppValidationError(
                    "heartbeat_at must not be earlier than started_at"
                )
            if self.lease_expires_at <= self.heartbeat_at:
                raise AppValidationError(
                    "lease_expires_at must be later than heartbeat_at"
                )
            if self.execution_attempts < 1:
                raise AppValidationError(
                    "running workflow must have an execution attempt"
                )
            return

        if any(value is not None for value in lease_values):
            raise AppValidationError(
                "non-running workflow must not retain an execution lease"
            )

    def _clear_execution_lease(self) -> None:
        """进入终态时释放租约，保留累计执行次数用于审计。"""
        self.lease_owner = None
        self.lease_expires_at = None
        self.heartbeat_at = None

    def _validate_audit_retention(self) -> None:
        """保证审计清理水位只出现在已经结束的工作流上。"""
        if self.audit_purged_at is None:
            return
        self._validate_timestamp("audit_purged_at", self.audit_purged_at)
        if self.ended_at is None:
            raise AppValidationError("audit_purged_at requires a terminal workflow")
        if self.audit_purged_at < self.ended_at:
            raise AppValidationError(
                "audit_purged_at must not be earlier than ended_at"
            )

    def _validate_cancellation_state(self) -> None:
        """允许历史取消态无元数据，但拒绝半套或非取消态取消事实。"""
        fields = (
            self.canceled_by,
            self.cancellation_reason,
            self.canceled_at,
            self.cancellation_idempotency_key_hash,
            self.cancellation_request_hash,
            self.cancellation_trace_id,
        )
        if not any(value is not None for value in fields):
            return
        if any(value is None for value in fields):
            raise AppValidationError("cancellation metadata must be complete")
        if self.status is not WorkflowRunStatus.CANCELED:
            raise AppValidationError(
                "cancellation metadata requires a canceled workflow"
            )
        assert self.canceled_by is not None
        assert self.cancellation_reason is not None
        assert self.canceled_at is not None
        assert self.cancellation_idempotency_key_hash is not None
        assert self.cancellation_request_hash is not None
        assert self.cancellation_trace_id is not None
        self._validate_text("canceled_by", self.canceled_by, 128)
        self._validate_text(
            "cancellation_reason",
            self.cancellation_reason,
            2048,
        )
        self._validate_timestamp("canceled_at", self.canceled_at)
        if self.ended_at is None or self.canceled_at != self.ended_at:
            raise AppValidationError("canceled_at must equal ended_at")
        self._validate_hash(
            "cancellation_idempotency_key_hash",
            self.cancellation_idempotency_key_hash,
        )
        self._validate_hash(
            "cancellation_request_hash",
            self.cancellation_request_hash,
        )
        self._validate_text(
            "cancellation_trace_id",
            self.cancellation_trace_id,
            128,
        )

    def _validate_status_timeline(self) -> None:
        """保证持久化状态与开始、结束时间一致。"""
        if self.status is WorkflowRunStatus.PENDING:
            valid = self.started_at is None and self.ended_at is None
        elif self.status is WorkflowRunStatus.RUNNING:
            valid = self.started_at is not None and self.ended_at is None
        elif self.status in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
        }:
            valid = self.started_at is not None and self.ended_at is not None
        else:
            valid = self.ended_at is not None
        if not valid:
            raise AppValidationError("workflow status and timestamps are inconsistent")

    def _validate_transition_time(self, now: datetime) -> None:
        """拒绝时间倒退，避免状态事件排序失真。"""
        self._validate_timestamp("now", now)
        if now < self.updated_at:
            raise AppValidationError("transition time must not move backwards")

    @staticmethod
    def _validate_text(field_name: str, value: str, max_length: int) -> None:
        """校验索引和审计字段。"""
        if not isinstance(value, str) or not 1 <= len(value) <= max_length:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {max_length}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )

    @staticmethod
    def _validate_hash(field_name: str, value: str) -> None:
        """要求SHA-256小写十六进制格式。"""
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise AppValidationError(f"{field_name} must be a SHA-256 hex digest")

    @staticmethod
    def _validate_timestamp(field_name: str, value: datetime) -> None:
        """要求状态时间携带时区。"""
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(f"{field_name} must include timezone information")
