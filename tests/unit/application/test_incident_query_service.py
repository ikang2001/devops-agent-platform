from datetime import UTC, datetime, timedelta
from types import TracebackType

import pytest

from devops_agent_platform.application.queries.incidents import (
    GetIncidentQuery,
    ListIncidentsQuery,
)
from devops_agent_platform.application.services.incident_query_service import (
    IncidentQueryService,
)
from devops_agent_platform.domain.enums import AlertSeverity, IncidentStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.incident import Incident

NOW = datetime(2026, 7, 2, 11, 0, tzinfo=UTC)


class FakeIncidentRepository:
    """按租户返回固定事故并记录查询参数。"""

    def __init__(
        self,
        incident: Incident | None,
        items: list[Incident] | None = None,
    ) -> None:
        self.incident = incident
        self.items = items or []
        self.calls: list[tuple[str, str]] = []
        self.list_calls: list[dict] = []

    async def get_by_id(
        self,
        incident_id: str,
        tenant_id: str,
    ) -> Incident | None:
        self.calls.append((incident_id, tenant_id))
        if (
            self.incident is not None
            and self.incident.incident_id == incident_id
            and self.incident.tenant_id == tenant_id
        ):
            return self.incident
        return None

    async def list_page(
        self,
        tenant_id: str,
        statuses: frozenset[IncidentStatus],
        *,
        before_updated_at: datetime | None,
        before_incident_id: str | None,
        limit: int,
    ) -> list[Incident]:
        self.list_calls.append(
            {
                "tenant_id": tenant_id,
                "statuses": statuses,
                "before_updated_at": before_updated_at,
                "before_incident_id": before_incident_id,
                "limit": limit,
            }
        )
        items = sorted(
            (
                item
                for item in self.items
                if item.tenant_id == tenant_id
                and item.status in statuses
            ),
            key=lambda item: (item.updated_at, item.incident_id),
            reverse=True,
        )
        if before_updated_at is not None:
            assert before_incident_id is not None
            items = [
                item
                for item in items
                if (item.updated_at, item.incident_id)
                < (before_updated_at, before_incident_id)
            ]
        return items[:limit]


class FakeUnitOfWork:
    """提供查询服务所需的只读事故仓储。"""

    def __init__(
        self,
        incident: Incident | None,
        items: list[Incident] | None = None,
    ) -> None:
        self.incidents = FakeIncidentRepository(incident, items)

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False


def build_resolved_incident() -> Incident:
    """构造包含历史敏感展示文本的已解决事故。"""
    return Incident(
        incident_id="inc_001",
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.RESOLVED,
        title="Checkout password=title-secret outage",
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
        version=2,
        resolved_by="admin_001",
        resolution_reason="Rotated token=reason-secret and verified.",
        resolved_at=NOW,
        resolution_idempotency_key_hash="a" * 64,
        resolution_request_hash="b" * 64,
        resolution_trace_id="trc_resolution_001",
    )


def build_closed_incident() -> Incident:
    """构造包含敏感关闭展示文本的已关闭事故。"""
    return Incident(
        incident_id="inc_001",
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.CLOSED,
        title="Checkout outage",
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
        version=3,
        resolved_by="admin_001",
        resolution_reason="Mitigation verified.",
        resolved_at=NOW - timedelta(minutes=30),
        resolution_idempotency_key_hash="a" * 64,
        resolution_request_hash="b" * 64,
        resolution_trace_id="trc_resolution_001",
        closed_by="admin_002",
        closure_reason="Closed after token=closure-secret review.",
        closed_at=NOW,
        closure_idempotency_key_hash="c" * 64,
        closure_request_hash="d" * 64,
        closure_trace_id="trc_closure_001",
    )


def build_open_incident(
    incident_id: str,
    updated_at: datetime,
) -> Incident:
    """构造列表分页使用的活动事故。"""
    return Incident(
        incident_id=incident_id,
        tenant_id="tenant_001",
        service_name="checkout-api",
        severity=AlertSeverity.WARNING,
        status=IncidentStatus.OPEN,
        title=f"Incident {incident_id}",
        created_at=NOW - timedelta(hours=2),
        updated_at=updated_at,
    )


async def test_query_returns_redacted_view_without_internal_hashes() -> None:
    """管理端读取完整解决事实，但不能看到敏感文本和内部幂等字段。"""
    unit_of_work = FakeUnitOfWork(build_resolved_incident())
    service = IncidentQueryService(lambda: unit_of_work)

    result = await service.get(
        GetIncidentQuery("tenant_001", "inc_001")
    )
    payload = result.to_dict()

    assert payload["status"] == IncidentStatus.RESOLVED.value
    assert payload["version"] == 2
    assert payload["title"] == "Checkout password=[REDACTED] outage"
    assert payload["resolution_reason"] == "Rotated token=[REDACTED] and verified."
    assert payload["resolved_at"] == NOW.isoformat()
    assert "resolution_idempotency_key_hash" not in payload
    assert "resolution_request_hash" not in payload
    assert "resolution_trace_id" not in payload
    assert "title-secret" not in str(payload)
    assert "reason-secret" not in str(payload)


async def test_query_escapes_del_from_historical_incident_text() -> None:
    """升级前脏记录中的 DEL 必须可见转义，不能原样进入响应。"""
    incident = build_resolved_incident()
    object.__setattr__(
        incident,
        "title",
        "Checkout\x7flegacy outage",
    )
    object.__setattr__(
        incident,
        "resolution_reason",
        "Mitigation\x7fverified.",
    )
    service = IncidentQueryService(lambda: FakeUnitOfWork(incident))

    payload = (
        await service.get(GetIncidentQuery("tenant_001", "inc_001"))
    ).to_dict()

    assert payload["title"] == "Checkout\\u007flegacy outage"
    assert payload["resolution_reason"] == "Mitigation\\u007fverified."
    assert "\x7f" not in str(payload)


async def test_query_returns_redacted_closure_without_internal_hashes() -> None:
    """管理端读取关闭事实，但不能看到关闭幂等字段和敏感文本。"""
    unit_of_work = FakeUnitOfWork(build_closed_incident())
    service = IncidentQueryService(lambda: unit_of_work)

    result = await service.get(
        GetIncidentQuery("tenant_001", "inc_001")
    )
    payload = result.to_dict()

    assert payload["status"] == IncidentStatus.CLOSED.value
    assert payload["version"] == 3
    assert payload["closed_by"] == "admin_002"
    assert payload["closure_reason"] == "Closed after token=[REDACTED] review."
    assert payload["closed_at"] == NOW.isoformat()
    assert "closure_idempotency_key_hash" not in payload
    assert "closure_request_hash" not in payload
    assert "closure_trace_id" not in payload
    assert "closure-secret" not in str(payload)


async def test_cross_tenant_query_returns_not_found() -> None:
    """跨租户查询不能泄露事故是否存在。"""
    unit_of_work = FakeUnitOfWork(build_resolved_incident())
    service = IncidentQueryService(lambda: unit_of_work)

    with pytest.raises(ResourceNotFound, match="Incident"):
        await service.get(
            GetIncidentQuery("tenant_other", "inc_001")
        )

    assert unit_of_work.incidents.calls == [
        ("inc_001", "tenant_other")
    ]


async def test_list_returns_stable_cursor_and_second_page() -> None:
    """服务多取一条生成游标，第二页从上一页末尾之后继续。"""
    items = [
        build_open_incident("inc_c", NOW),
        build_open_incident("inc_b", NOW),
        build_open_incident("inc_a", NOW - timedelta(minutes=1)),
    ]
    unit_of_work = FakeUnitOfWork(None, items)
    service = IncidentQueryService(lambda: unit_of_work)

    first = await service.list(
        ListIncidentsQuery("tenant_001", limit=2)
    )
    assert [item.incident_id for item in first.items] == [
        "inc_c",
        "inc_b",
    ]
    assert first.next_cursor is not None
    assert unit_of_work.incidents.list_calls[0]["limit"] == 3

    second = await service.list(
        ListIncidentsQuery(
            "tenant_001",
            limit=2,
            cursor=first.next_cursor,
        )
    )
    assert [item.incident_id for item in second.items] == ["inc_a"]
    assert second.next_cursor is None
    assert unit_of_work.incidents.list_calls[1][
        "before_incident_id"
    ] == "inc_b"


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!",
        "e30",
        "eyJ1cGRhdGVkX2F0IjoiYmFkIiwiaW5jaWRlbnRfaWQiOiJpbmNfMSJ9",
        (
            "eyJpbmNpZGVudF9pZCI6ImluY18xXHUwMDdmIiwidXBkYXRlZF9h"
            "dCI6IjIwMjYtMDctMDJUMTE6MDA6MDArMDA6MDAifQ"
        ),
    ],
)
async def test_list_rejects_invalid_cursor(cursor: str) -> None:
    """篡改、缺字段或非法时间游标不能进入仓储。"""
    unit_of_work = FakeUnitOfWork(None)
    service = IncidentQueryService(lambda: unit_of_work)

    with pytest.raises(AppValidationError, match="cursor"):
        await service.list(
            ListIncidentsQuery("tenant_001", cursor=cursor)
        )

    assert unit_of_work.incidents.list_calls == []


@pytest.mark.parametrize(
    "query",
    [
        (" tenant_001", "inc_001"),
        ("tenant_001", "inc 001"),
        ("tenant_001", ""),
    ],
)
def test_query_rejects_invalid_identity(query: tuple[str, str]) -> None:
    """查询契约不能依赖 Path 校验才拒绝脏身份字段。"""
    with pytest.raises(AppValidationError):
        GetIncidentQuery(*query)


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "tenant 001"},
        {"statuses": frozenset()},
        {"statuses": frozenset({"OPEN"})},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"cursor": " "},
        {"cursor": "abc\x7f"},
    ],
)
def test_list_query_rejects_invalid_boundaries(
    changes: dict[str, object],
) -> None:
    """列表查询在应用边界独立限制状态、容量和游标外壳。"""
    values: dict[str, object] = {
        "tenant_id": "tenant_001",
        "statuses": frozenset({IncidentStatus.OPEN}),
        "limit": 50,
        "cursor": None,
    }
    values.update(changes)

    with pytest.raises(AppValidationError):
        ListIncidentsQuery(**values)  # type: ignore[arg-type]
