from dataclasses import dataclass, field

from devops_agent_platform.domain.enums import IncidentStatus
from devops_agent_platform.domain.exceptions import AppValidationError


def _contains_ascii_control(value: str) -> bool:
    """识别不可见ASCII控制字符，避免身份和游标污染审计链路。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


@dataclass(frozen=True)
class GetIncidentQuery:
    """按租户和事故 ID 读取单个事故聚合。"""

    tenant_id: str
    incident_id: str

    def __post_init__(self) -> None:
        """校验查询身份，拒绝空值和歧义空白。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("incident_id", self.incident_id, 64),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or _contains_ascii_control(value)
                or any(character.isspace() for character in value)
            ):
                raise AppValidationError(f"{field_name} is invalid")


@dataclass(frozen=True)
class ListIncidentsQuery:
    """按租户和状态读取一个有界 Incident 页面。"""

    tenant_id: str
    statuses: frozenset[IncidentStatus] = field(
        default_factory=lambda: frozenset(IncidentStatus)
    )
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        """校验租户、状态集合、页面容量和不透明游标外壳。"""
        if (
            not isinstance(self.tenant_id, str)
            or not 1 <= len(self.tenant_id) <= 128
            or self.tenant_id != self.tenant_id.strip()
            or _contains_ascii_control(self.tenant_id)
            or any(
                character.isspace()
                for character in self.tenant_id
            )
        ):
            raise AppValidationError("tenant_id is invalid")
        if (
            not isinstance(self.statuses, frozenset)
            or not self.statuses
            or not all(
                isinstance(status, IncidentStatus)
                for status in self.statuses
            )
        ):
            raise AppValidationError("statuses is invalid")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 100
        ):
            raise AppValidationError("limit must be between 1 and 100")
        if self.cursor is not None and (
            not isinstance(self.cursor, str)
            or not 1 <= len(self.cursor) <= 1024
            or self.cursor != self.cursor.strip()
            or _contains_ascii_control(self.cursor)
            or any(character.isspace() for character in self.cursor)
        ):
            raise AppValidationError("cursor is invalid")
