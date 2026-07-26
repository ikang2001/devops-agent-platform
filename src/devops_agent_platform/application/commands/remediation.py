from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class CreateRemediationPlanCommand:
    tenant_id: str
    workflow_run_id: str
    action_key: str
    target: str
    evidence_ids: tuple[str, ...]
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        _validate_common(self)
        if (
            not isinstance(self.evidence_ids, tuple)
            or not 4 <= len(self.evidence_ids) <= 100
            or len(set(self.evidence_ids)) != len(self.evidence_ids)
        ):
            raise AppValidationError(
                "evidence_ids must contain 4 to 100 unique identifiers"
            )
        for value in self.evidence_ids:
            _validate_text("evidence_id", value, 64)


@dataclass(frozen=True)
class DecideRemediationPlanCommand:
    tenant_id: str
    remediation_plan_id: str
    expected_version: int
    approved: bool
    reason: str
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        _validate_common(self)
        _validate_version(self.expected_version)
        if not isinstance(self.approved, bool):
            raise AppValidationError("approved must be a boolean")
        _validate_multiline("reason", self.reason, 4096)


@dataclass(frozen=True)
class ExecuteRemediationPlanCommand:
    tenant_id: str
    remediation_plan_id: str
    expected_version: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        _validate_common(self)
        _validate_version(self.expected_version)


@dataclass(frozen=True)
class RollbackRemediationPlanCommand(ExecuteRemediationPlanCommand):
    pass


def _validate_common(command: object) -> None:
    for name, maximum in (
        ("tenant_id", 128),
        ("workflow_run_id", 64),
        ("remediation_plan_id", 64),
        ("action_key", 128),
        ("target", 256),
        ("idempotency_key", 128),
        ("requested_by", 128),
        ("trace_id", 128),
    ):
        if hasattr(command, name):
            _validate_text(name, getattr(command, name), maximum)


def _validate_text(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")


def _validate_multiline(name: str, value: str, maximum: int) -> None:
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
        raise AppValidationError(f"{name} is invalid")


def _validate_version(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AppValidationError("expected_version must be a positive integer")
