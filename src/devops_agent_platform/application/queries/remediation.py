from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class GetRemediationPlanQuery:
    tenant_id: str
    remediation_plan_id: str

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("remediation_plan_id", self.remediation_plan_id, 64),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in value
                )
            ):
                raise AppValidationError(f"{name} is invalid")
