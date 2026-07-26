from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class SendWorkflowNotificationCommand:
    """把成功 RCA 摘要投递到显式目标的应用命令。"""

    tenant_id: str
    workflow_run_id: str
    target_system: str
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("target_system", self.target_system, 32),
            ("idempotency_key", self.idempotency_key, 256),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{name} is invalid")
        if self.target_system not in {"slack", "teams", "pagerduty"}:
            raise AppValidationError("target_system is unsupported")
