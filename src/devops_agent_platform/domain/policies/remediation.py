from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)


@dataclass(frozen=True)
class RemediationExecutionPolicy:
    """执行总开关、租户和 UTC 维护窗口策略。"""

    execution_enabled: bool = False
    allowed_tenants: frozenset[str] = frozenset()
    maintenance_start_hour_utc: int = 0
    maintenance_end_hour_utc: int = 24

    def __post_init__(self) -> None:
        if not isinstance(self.execution_enabled, bool):
            raise AppValidationError("execution_enabled must be a boolean")
        if (
            isinstance(self.maintenance_start_hour_utc, bool)
            or not isinstance(self.maintenance_start_hour_utc, int)
            or not 0 <= self.maintenance_start_hour_utc <= 23
            or isinstance(self.maintenance_end_hour_utc, bool)
            or not isinstance(self.maintenance_end_hour_utc, int)
            or not 1 <= self.maintenance_end_hour_utc <= 24
            or self.maintenance_start_hour_utc
            >= self.maintenance_end_hour_utc
        ):
            raise AppValidationError("maintenance window is invalid")
        for tenant_id in self.allowed_tenants:
            _validate_key("tenant_id", tenant_id, 128)
    def authorize_execution(
        self,
        *,
        tenant_id: str,
        now: datetime,
    ) -> None:
        if not self.execution_enabled:
            raise ConflictError("Remediation execution kill switch is disabled")
        if tenant_id not in self.allowed_tenants:
            raise ConflictError("Tenant is not allowed to execute remediation")
        if now.tzinfo is None or now.utcoffset() is None:
            raise AppValidationError("clock must return a timezone-aware datetime")
        hour = now.utctimetuple().tm_hour
        if not self.maintenance_start_hour_utc <= hour < self.maintenance_end_hour_utc:
            raise ConflictError("Remediation is outside the maintenance window")


def _validate_key(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")
