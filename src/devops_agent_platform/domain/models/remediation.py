from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from hashlib import sha256

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)


class RemediationStatus(StrEnum):
    """受审批修复计划的有限状态机。"""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


class RemediationRisk(StrEnum):
    """修复动作风险级别；所有级别都必须人工审批。"""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class RemediationPlan:
    """只引用预注册动作键的修复计划与完整审批/执行审计快照。

    执行与回滚中间态携带 attempt 与 lease。迟到的 finish 必须匹配 owner 与
    attempt，过期 lease 只能通过显式 stale fail 回收，不能被旧 owner 收口。
    """

    remediation_plan_id: str
    tenant_id: str
    workflow_run_id: str
    incident_id: str
    action_key: str
    target: str
    expected_effect: str
    risk: RemediationRisk
    rollback_action_key: str
    evidence_ids: tuple[str, ...]
    dry_run_summary: str
    created_by: str
    created_at: datetime
    trace_id: str
    create_idempotency_key_hash: str
    create_request_hash: str
    status: RemediationStatus = RemediationStatus.DRAFT
    version: int = 1
    decided_by: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    decision_trace_id: str | None = None
    decision_idempotency_key_hash: str | None = None
    execution_summary: str | None = None
    execution_started_at: datetime | None = None
    executed_at: datetime | None = None
    executed_by: str | None = None
    execution_trace_id: str | None = None
    execution_idempotency_key_hash: str | None = None
    execution_attempt: int = 0
    execution_lease_expires_at: datetime | None = None
    rollback_summary: str | None = None
    rollback_started_at: datetime | None = None
    rolled_back_at: datetime | None = None
    rolled_back_by: str | None = None
    rollback_trace_id: str | None = None
    rollback_idempotency_key_hash: str | None = None
    rollback_attempt: int = 0
    rollback_lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("remediation_plan_id", self.remediation_plan_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("incident_id", self.incident_id, 64),
            ("action_key", self.action_key, 128),
            ("target", self.target, 256),
            ("rollback_action_key", self.rollback_action_key, 128),
            ("created_by", self.created_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            _validate_single_line(field_name, value, maximum)
        _validate_multiline(
            "expected_effect",
            self.expected_effect,
            2048,
        )
        _validate_multiline(
            "dry_run_summary",
            self.dry_run_summary,
            4096,
        )
        if not isinstance(self.risk, RemediationRisk):
            raise AppValidationError("risk is invalid")
        if not isinstance(self.status, RemediationStatus):
            raise AppValidationError("status is invalid")
        if (
            not isinstance(self.evidence_ids, tuple)
            or not 4 <= len(self.evidence_ids) <= 100
            or len(set(self.evidence_ids)) != len(self.evidence_ids)
        ):
            raise AppValidationError(
                "evidence_ids must contain 4 to 100 unique identifiers"
            )
        for evidence_id in self.evidence_ids:
            _validate_single_line("evidence_id", evidence_id, 64)
        _validate_time("created_at", self.created_at)
        _validate_hash(
            "create_idempotency_key_hash",
            self.create_idempotency_key_hash,
        )
        _validate_hash("create_request_hash", self.create_request_hash)
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or not 1 <= self.version <= 6
        ):
            raise AppValidationError("version is invalid")
        _validate_attempt("execution_attempt", self.execution_attempt)
        _validate_attempt("rollback_attempt", self.rollback_attempt)
        self._validate_optional_fields()
        self._validate_state_shape()

    def decide(
        self,
        *,
        approved: bool,
        requested_by: str,
        reason: str,
        decided_at: datetime,
        trace_id: str,
        idempotency_key: str,
    ) -> "RemediationPlan":
        """人工批准或拒绝明确版本的计划。"""
        idempotency_hash = _hash_idempotency_key(idempotency_key)
        if self.status is not RemediationStatus.DRAFT:
            if self.decision_idempotency_key_hash == idempotency_hash:
                return self
            raise ConflictError("Remediation plan has already been decided")
        return replace(
            self,
            status=(
                RemediationStatus.APPROVED
                if approved
                else RemediationStatus.REJECTED
            ),
            version=2,
            decided_by=requested_by,
            decision_reason=reason,
            decided_at=decided_at,
            decision_trace_id=trace_id,
            decision_idempotency_key_hash=idempotency_hash,
        )

    def start_execution(
        self,
        *,
        requested_by: str,
        started_at: datetime,
        lease_expires_at: datetime,
        trace_id: str,
        idempotency_key: str,
    ) -> "RemediationPlan":
        """在外部写操作前持久化带租约的执行认领。"""
        idempotency_hash = _hash_idempotency_key(idempotency_key)
        _validate_time("started_at", started_at)
        _validate_time("lease_expires_at", lease_expires_at)
        if lease_expires_at <= started_at:
            raise AppValidationError("execution lease must end after start")
        if self.status is RemediationStatus.EXECUTING:
            if (
                self.execution_idempotency_key_hash == idempotency_hash
                and self.executed_by == requested_by
            ):
                return self
            raise ConflictError("Remediation plan is already executing")
        if self.status is not RemediationStatus.APPROVED:
            if self.execution_idempotency_key_hash == idempotency_hash:
                return self
            raise ConflictError("Remediation plan is not approved")
        return replace(
            self,
            status=RemediationStatus.EXECUTING,
            version=3,
            execution_started_at=started_at,
            executed_by=requested_by,
            execution_trace_id=trace_id,
            execution_idempotency_key_hash=idempotency_hash,
            execution_attempt=1,
            execution_lease_expires_at=lease_expires_at,
            execution_summary=None,
            executed_at=None,
        )

    def finish_execution(
        self,
        *,
        succeeded: bool,
        summary: str,
        completed_at: datetime,
        expected_attempt: int,
        requested_by: str,
    ) -> "RemediationPlan":
        """记录预注册动作执行结果；必须匹配 owner 与 attempt。"""
        if self.status is not RemediationStatus.EXECUTING:
            raise ConflictError("Remediation plan is not executing")
        if self.execution_attempt != expected_attempt:
            raise ConflictError("Remediation execution attempt fence mismatch")
        if self.executed_by != requested_by:
            raise ConflictError("Remediation execution owner fence mismatch")
        return replace(
            self,
            status=(
                RemediationStatus.SUCCEEDED
                if succeeded
                else RemediationStatus.FAILED
            ),
            version=4,
            execution_summary=summary,
            executed_at=completed_at,
            execution_lease_expires_at=None,
        )

    def fail_stale_execution(
        self,
        *,
        now: datetime,
        summary: str,
    ) -> "RemediationPlan":
        """租约过期后把卡住的 EXECUTING 收口为 FAILED。"""
        _validate_time("now", now)
        if self.status is not RemediationStatus.EXECUTING:
            raise ConflictError("Remediation plan is not executing")
        if (
            self.execution_lease_expires_at is None
            or self.execution_lease_expires_at > now
        ):
            raise ConflictError("Remediation execution lease has not expired")
        return replace(
            self,
            status=RemediationStatus.FAILED,
            version=4,
            execution_summary=summary,
            executed_at=now,
            execution_lease_expires_at=None,
        )

    def start_rollback(
        self,
        *,
        requested_by: str,
        started_at: datetime,
        lease_expires_at: datetime,
        trace_id: str,
        idempotency_key: str,
    ) -> "RemediationPlan":
        """只有成功执行的计划才能进入带租约的回滚。"""
        idempotency_hash = _hash_idempotency_key(idempotency_key)
        _validate_time("started_at", started_at)
        _validate_time("lease_expires_at", lease_expires_at)
        if lease_expires_at <= started_at:
            raise AppValidationError("rollback lease must end after start")
        if self.status is RemediationStatus.ROLLING_BACK:
            if (
                self.rollback_idempotency_key_hash == idempotency_hash
                and self.rolled_back_by == requested_by
            ):
                return self
            raise ConflictError("Remediation plan is already rolling back")
        if self.status is not RemediationStatus.SUCCEEDED:
            if self.rollback_idempotency_key_hash == idempotency_hash:
                return self
            raise ConflictError("Remediation plan cannot be rolled back")
        return replace(
            self,
            status=RemediationStatus.ROLLING_BACK,
            version=5,
            rollback_started_at=started_at,
            rolled_back_by=requested_by,
            rollback_trace_id=trace_id,
            rollback_idempotency_key_hash=idempotency_hash,
            rollback_attempt=1,
            rollback_lease_expires_at=lease_expires_at,
            rollback_summary=None,
            rolled_back_at=None,
        )

    def finish_rollback(
        self,
        *,
        succeeded: bool,
        summary: str,
        completed_at: datetime,
        expected_attempt: int,
        requested_by: str,
    ) -> "RemediationPlan":
        """记录回滚结果；必须匹配 owner 与 attempt。"""
        if self.status is not RemediationStatus.ROLLING_BACK:
            raise ConflictError("Remediation plan is not rolling back")
        if self.rollback_attempt != expected_attempt:
            raise ConflictError("Remediation rollback attempt fence mismatch")
        if self.rolled_back_by != requested_by:
            raise ConflictError("Remediation rollback owner fence mismatch")
        return replace(
            self,
            status=(
                RemediationStatus.ROLLED_BACK
                if succeeded
                else RemediationStatus.ROLLBACK_FAILED
            ),
            version=6,
            rollback_summary=summary,
            rolled_back_at=completed_at,
            rollback_lease_expires_at=None,
        )

    def fail_stale_rollback(
        self,
        *,
        now: datetime,
        summary: str,
    ) -> "RemediationPlan":
        """租约过期后把卡住的 ROLLING_BACK 收口为 ROLLBACK_FAILED。"""
        _validate_time("now", now)
        if self.status is not RemediationStatus.ROLLING_BACK:
            raise ConflictError("Remediation plan is not rolling back")
        if (
            self.rollback_lease_expires_at is None
            or self.rollback_lease_expires_at > now
        ):
            raise ConflictError("Remediation rollback lease has not expired")
        return replace(
            self,
            status=RemediationStatus.ROLLBACK_FAILED,
            version=6,
            rollback_summary=summary,
            rolled_back_at=now,
            rollback_lease_expires_at=None,
        )

    def execution_lease_active(self, now: datetime) -> bool:
        """判断执行租约是否仍有效。"""
        _validate_time("now", now)
        return (
            self.status is RemediationStatus.EXECUTING
            and self.execution_lease_expires_at is not None
            and self.execution_lease_expires_at > now
        )

    def rollback_lease_active(self, now: datetime) -> bool:
        """判断回滚租约是否仍有效。"""
        _validate_time("now", now)
        return (
            self.status is RemediationStatus.ROLLING_BACK
            and self.rollback_lease_expires_at is not None
            and self.rollback_lease_expires_at > now
        )

    def _validate_optional_fields(self) -> None:
        for field_name, value, maximum in (
            ("decided_by", self.decided_by, 128),
            ("decision_trace_id", self.decision_trace_id, 128),
            ("executed_by", self.executed_by, 128),
            ("execution_trace_id", self.execution_trace_id, 128),
            ("rolled_back_by", self.rolled_back_by, 128),
            ("rollback_trace_id", self.rollback_trace_id, 128),
        ):
            if value is not None:
                _validate_single_line(field_name, value, maximum)
        for field_name, value in (
            ("decision_reason", self.decision_reason),
            ("execution_summary", self.execution_summary),
            ("rollback_summary", self.rollback_summary),
        ):
            if value is not None:
                _validate_multiline(field_name, value, 4096)
        for field_name, value in (
            ("decided_at", self.decided_at),
            ("execution_started_at", self.execution_started_at),
            ("executed_at", self.executed_at),
            ("execution_lease_expires_at", self.execution_lease_expires_at),
            ("rollback_started_at", self.rollback_started_at),
            ("rolled_back_at", self.rolled_back_at),
            ("rollback_lease_expires_at", self.rollback_lease_expires_at),
        ):
            if value is not None:
                _validate_time(field_name, value)
        for field_name, value in (
            (
                "decision_idempotency_key_hash",
                self.decision_idempotency_key_hash,
            ),
            (
                "execution_idempotency_key_hash",
                self.execution_idempotency_key_hash,
            ),
            (
                "rollback_idempotency_key_hash",
                self.rollback_idempotency_key_hash,
            ),
        ):
            if value is not None:
                _validate_hash(field_name, value)

    def _validate_state_shape(self) -> None:
        decision = (
            self.decided_by,
            self.decision_reason,
            self.decided_at,
            self.decision_trace_id,
            self.decision_idempotency_key_hash,
        )
        execution_start = (
            self.execution_started_at,
            self.executed_by,
            self.execution_trace_id,
            self.execution_idempotency_key_hash,
        )
        rollback_start = (
            self.rollback_started_at,
            self.rolled_back_by,
            self.rollback_trace_id,
            self.rollback_idempotency_key_hash,
        )
        expected_version = {
            RemediationStatus.DRAFT: 1,
            RemediationStatus.APPROVED: 2,
            RemediationStatus.REJECTED: 2,
            RemediationStatus.EXECUTING: 3,
            RemediationStatus.SUCCEEDED: 4,
            RemediationStatus.FAILED: 4,
            RemediationStatus.ROLLING_BACK: 5,
            RemediationStatus.ROLLED_BACK: 6,
            RemediationStatus.ROLLBACK_FAILED: 6,
        }[self.status]
        if self.version != expected_version:
            raise AppValidationError(
                "version does not match remediation status"
            )
        if self.status is RemediationStatus.DRAFT:
            if any(value is not None for value in decision):
                raise AppValidationError("draft contains decision fields")
            if (
                self.execution_attempt != 0
                or self.rollback_attempt != 0
                or self.execution_lease_expires_at is not None
                or self.rollback_lease_expires_at is not None
            ):
                raise AppValidationError("draft contains lease fields")
            return
        if any(value is None for value in decision):
            raise AppValidationError("decided plan is incomplete")
        if self.status in {
            RemediationStatus.APPROVED,
            RemediationStatus.REJECTED,
        }:
            if any(value is not None for value in execution_start):
                raise AppValidationError(
                    "unexecuted plan contains execution fields"
                )
            if (
                self.execution_attempt != 0
                or self.rollback_attempt != 0
                or self.execution_lease_expires_at is not None
                or self.rollback_lease_expires_at is not None
            ):
                raise AppValidationError(
                    "unexecuted plan contains lease fields"
                )
            return
        if any(value is None for value in execution_start):
            raise AppValidationError("executing plan is incomplete")
        if self.execution_attempt < 1:
            raise AppValidationError("execution attempt must be positive")
        if self.status is RemediationStatus.EXECUTING:
            if self.executed_at is not None or self.execution_summary is not None:
                raise AppValidationError("executing plan contains result fields")
            if self.execution_lease_expires_at is None:
                raise AppValidationError("executing plan requires a lease")
            if self.execution_lease_expires_at <= self.execution_started_at:
                raise AppValidationError("execution lease window is invalid")
            if self.rollback_attempt != 0 or self.rollback_lease_expires_at is not None:
                raise AppValidationError("executing plan contains rollback lease")
            return
        if self.executed_at is None or self.execution_summary is None:
            raise AppValidationError("executed plan is incomplete")
        if self.execution_lease_expires_at is not None:
            raise AppValidationError("completed execution still holds a lease")
        if self.status in {
            RemediationStatus.SUCCEEDED,
            RemediationStatus.FAILED,
        }:
            if any(value is not None for value in rollback_start):
                raise AppValidationError(
                    "plan contains unexpected rollback fields"
                )
            if self.rollback_attempt != 0 or self.rollback_lease_expires_at is not None:
                raise AppValidationError(
                    "plan contains unexpected rollback lease fields"
                )
            return
        if any(value is None for value in rollback_start):
            raise AppValidationError("rollback plan is incomplete")
        if self.rollback_attempt < 1:
            raise AppValidationError("rollback attempt must be positive")
        if self.status is RemediationStatus.ROLLING_BACK:
            if self.rolled_back_at is not None or self.rollback_summary is not None:
                raise AppValidationError("rollback contains result fields")
            if self.rollback_lease_expires_at is None:
                raise AppValidationError("rolling back plan requires a lease")
            if self.rollback_lease_expires_at <= self.rollback_started_at:
                raise AppValidationError("rollback lease window is invalid")
            return
        if self.rolled_back_at is None or self.rollback_summary is None:
            raise AppValidationError("completed rollback is incomplete")
        if self.rollback_lease_expires_at is not None:
            raise AppValidationError("completed rollback still holds a lease")


def _hash_idempotency_key(value: str) -> str:
    _validate_single_line("idempotency_key", value, 128)
    return sha256(value.encode()).hexdigest()


def _validate_attempt(field_name: str, value: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 1000
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _validate_single_line(
    field_name: str,
    value: str,
    maximum: int,
) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _validate_multiline(
    field_name: str,
    value: str,
    maximum: int,
) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(
            (ord(character) < 32 and character != "\n")
            or ord(character) == 127
            for character in value
        )
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _validate_hash(field_name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _validate_time(field_name: str, value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise AppValidationError(f"{field_name} must include timezone")
