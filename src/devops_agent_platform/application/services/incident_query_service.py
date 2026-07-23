import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from devops_agent_platform.application.queries.incidents import (
    GetIncidentQuery,
    ListIncidentsQuery,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]


def _contains_ascii_control(value: str) -> bool:
    """识别不可见ASCII控制字符，包含DEL。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


@dataclass(frozen=True)
class IncidentView:
    """管理端可读取且不含内部幂等摘要的事故视图。"""

    incident_id: str
    tenant_id: str
    service_name: str
    severity: str
    status: str
    title: str
    created_at: datetime
    updated_at: datetime
    version: int
    resolved_by: str | None
    resolution_reason: str | None
    resolved_at: datetime | None
    closed_by: str | None = None
    closure_reason: str | None = None
    closed_at: datetime | None = None

    @classmethod
    def from_domain(cls, incident: Incident) -> "IncidentView":
        """从聚合提取稳定字段，并兜底清洗历史展示文本。"""
        return cls(
            incident_id=incident.incident_id,
            tenant_id=incident.tenant_id,
            service_name=incident.service_name,
            severity=incident.severity.value,
            status=incident.status.value,
            title=_safe_output_text(
                incident.title,
                maximum=512,
                multiline=False,
            ),
            created_at=incident.created_at,
            updated_at=incident.updated_at,
            version=incident.version,
            resolved_by=incident.resolved_by,
            resolution_reason=(
                _safe_output_text(
                    incident.resolution_reason,
                    maximum=2048,
                    multiline=True,
                )
                if incident.resolution_reason is not None
                else None
            ),
            resolved_at=incident.resolved_at,
            closed_by=incident.closed_by,
            closure_reason=(
                _safe_output_text(
                    incident.closure_reason,
                    maximum=2048,
                    multiline=True,
                )
                if incident.closure_reason is not None
                else None
            ),
            closed_at=incident.closed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为统一响应 envelope 中的 data。"""
        return {
            "incident_id": self.incident_id,
            "tenant_id": self.tenant_id,
            "service_name": self.service_name,
            "severity": self.severity,
            "status": self.status,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "version": self.version,
            "resolved_by": self.resolved_by,
            "resolution_reason": self.resolution_reason,
            "resolved_at": (
                self.resolved_at.isoformat()
                if self.resolved_at is not None
                else None
            ),
            "closed_by": self.closed_by,
            "closure_reason": self.closure_reason,
            "closed_at": (
                self.closed_at.isoformat()
                if self.closed_at is not None
                else None
            ),
        }


@dataclass(frozen=True)
class IncidentListView:
    """一个有界 Incident 页面及下一页不透明游标。"""

    items: tuple[IncidentView, ...]
    next_cursor: str | None

    def to_dict(self) -> dict[str, Any]:
        """转换为可扩展的列表响应结构。"""
        return {
            "items": [item.to_dict() for item in self.items],
            "next_cursor": self.next_cursor,
        }


class IncidentQueryService:
    """按租户读取单个事故的安全管理视图。"""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def get(self, query: GetIncidentQuery) -> IncidentView:
        """按租户边界加载事故，跨租户与不存在统一返回未找到。"""
        async with self._unit_of_work_factory() as unit_of_work:
            incident = await unit_of_work.incidents.get_by_id(
                query.incident_id,
                query.tenant_id,
            )
            if incident is None:
                raise ResourceNotFound("Incident not found")
            return IncidentView.from_domain(incident)

    async def list(
        self,
        query: ListIncidentsQuery,
    ) -> IncidentListView:
        """按稳定 Keyset 游标读取一个有界事故页面。"""
        before_updated_at, before_incident_id = _decode_cursor(
            query.cursor
        )
        async with self._unit_of_work_factory() as unit_of_work:
            incidents = await unit_of_work.incidents.list_page(
                query.tenant_id,
                query.statuses,
                before_updated_at=before_updated_at,
                before_incident_id=before_incident_id,
                limit=query.limit + 1,
            )
        has_more = len(incidents) > query.limit
        visible = incidents[: query.limit]
        return IncidentListView(
            items=tuple(
                IncidentView.from_domain(incident)
                for incident in visible
            ),
            next_cursor=(
                _encode_cursor(visible[-1])
                if has_more and visible
                else None
            ),
        )


def _encode_cursor(incident: Incident) -> str:
    """把最后一条排序键编码成 URL-safe 不透明游标。"""
    encoded = json.dumps(
        {
            "updated_at": incident.updated_at.isoformat(),
            "incident_id": incident.incident_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def _decode_cursor(
    cursor: str | None,
) -> tuple[datetime | None, str | None]:
    """严格解码游标，拒绝结构漂移和无时区时间。"""
    if cursor is None:
        return None, None
    padding = "=" * (-len(cursor) % 4)
    try:
        decoded = base64.b64decode(
            f"{cursor}{padding}",
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        payload = json.loads(decoded)
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise AppValidationError("cursor is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"updated_at", "incident_id"}
        or not isinstance(payload["updated_at"], str)
        or not isinstance(payload["incident_id"], str)
    ):
        raise AppValidationError("cursor is invalid")
    try:
        updated_at = datetime.fromisoformat(payload["updated_at"])
    except ValueError as exc:
        raise AppValidationError("cursor is invalid") from exc
    incident_id = payload["incident_id"]
    if (
        updated_at.tzinfo is None
        or updated_at.utcoffset() is None
        or not 1 <= len(incident_id) <= 64
        or incident_id != incident_id.strip()
        or _contains_ascii_control(incident_id)
        or any(character.isspace() for character in incident_id)
    ):
        raise AppValidationError("cursor is invalid")
    return updated_at.astimezone(UTC), incident_id


def _safe_output_text(
    value: str,
    *,
    maximum: int,
    multiline: bool,
) -> str:
    """查询输出前遮蔽敏感片段并转义不可见控制字符。"""
    redacted = redact_sensitive_text(value.strip())[0]
    result: list[str] = []
    for character in redacted:
        if character == "\n" and multiline:
            result.append(character)
        elif ord(character) < 32 or ord(character) == 127:
            result.append(f"\\u{ord(character):04x}")
        else:
            result.append(character)
    safe_value = "".join(result)[:maximum].strip()
    return safe_value or "[REDACTED]"
