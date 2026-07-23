from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class StartRCACommand:
    """启动 RCA 工作流用例的应用层入参。"""

    incident_id: str
    tenant_id: str
    operator_id: str
    idempotency_key: str
    trace_id: str

    def __post_init__(self) -> None:
        """阻止内部调用绕过HTTP校验传入脏标识。"""
        limits = {
            "incident_id": (self.incident_id, 64),
            "tenant_id": (self.tenant_id, 128),
            "operator_id": (self.operator_id, 128),
            "idempotency_key": (self.idempotency_key, 128),
            "trace_id": (self.trace_id, 128),
        }
        for field_name, (value, maximum) in limits.items():
            if not isinstance(value, str):
                raise AppValidationError(f"{field_name} must be a string")
            if not 1 <= len(value) <= maximum:
                raise AppValidationError(
                    f"{field_name} length must be between 1 and {maximum}"
                )
            if value != value.strip():
                raise AppValidationError(
                    f"{field_name} must not contain surrounding whitespace"
                )
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise AppValidationError(
                    f"{field_name} must not contain control characters"
                )


@dataclass(frozen=True)
class CancelRCAWorkflowCommand:
    """管理员取消单次 RCA 工作流的应用层入参。"""

    tenant_id: str
    workflow_run_id: str
    expected_version: int
    reason: str
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验租户、运行版本、取消原因和审计身份。"""
        limits = {
            "tenant_id": (self.tenant_id, 128),
            "workflow_run_id": (self.workflow_run_id, 64),
            "idempotency_key": (self.idempotency_key, 128),
            "requested_by": (self.requested_by, 128),
            "trace_id": (self.trace_id, 128),
        }
        for field_name, (value, maximum) in limits.items():
            if not isinstance(value, str):
                raise AppValidationError(f"{field_name} must be a string")
            if not 1 <= len(value) <= maximum:
                raise AppValidationError(
                    f"{field_name} length must be between 1 and {maximum}"
                )
            if value != value.strip() or any(
                character.isspace() or ord(character) == 127 for character in value
            ):
                raise AppValidationError(f"{field_name} is invalid")
        if (
            isinstance(self.expected_version, bool)
            or not isinstance(self.expected_version, int)
            or self.expected_version < 1
        ):
            raise AppValidationError("expected_version must be a positive integer")
        if (
            not isinstance(self.reason, str)
            or not 1 <= len(self.reason) <= 2048
            or self.reason != self.reason.strip()
            or any(
                (ord(character) < 32 and character != "\n") or ord(character) == 127
                for character in self.reason
            )
        ):
            raise AppValidationError("cancellation reason is invalid")
