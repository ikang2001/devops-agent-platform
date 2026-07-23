from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class ResolveIncidentCommand:
    """由管理员把一个明确版本的事故推进到已解决状态。"""

    tenant_id: str
    incident_id: str
    expected_version: int
    reason: str
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验租户、事故版本、解决原因和审计身份。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("incident_id", self.incident_id, 64),
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
            raise AppValidationError("resolution reason is invalid")


@dataclass(frozen=True)
class CloseIncidentCommand:
    """由管理员把一个已解决事故推进到关闭状态。"""

    tenant_id: str
    incident_id: str
    expected_version: int
    reason: str
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验租户、事故版本、关闭原因和审计身份。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("incident_id", self.incident_id, 64),
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
            raise AppValidationError("closure reason is invalid")
