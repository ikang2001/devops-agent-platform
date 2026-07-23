from dataclasses import dataclass
from typing import Protocol

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class ObservabilityTarget:
    """从可信业务数据解析出的可观测查询目标。"""

    tenant_id: str
    incident_id: str
    service_name: str

    def __post_init__(self) -> None:
        """校验租户、事故和服务标识。"""
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("incident_id", self.incident_id, 64)
        self._validate_text("service_name", self.service_name, 256)

    @staticmethod
    def _validate_text(
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


class ObservabilityTargetResolverPort(Protocol):
    """按租户和事故解析可信服务目标。"""

    async def resolve(
        self,
        tenant_id: str,
        incident_id: str,
    ) -> ObservabilityTarget:
        """目标不存在时抛出ResourceNotFound。"""
        ...
