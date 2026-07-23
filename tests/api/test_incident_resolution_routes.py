from datetime import UTC, datetime

from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.incident_query_service import (
    IncidentListView,
    IncidentView,
)
from devops_agent_platform.application.services.incident_resolution_service import (
    IncidentClosureResult,
    IncidentResolutionResult,
)
from devops_agent_platform.bootstrap.app import create_app

INCIDENT_URL = (
    "/api/v1/admin/tenants/tenant_001/incidents/inc_001"
)
URL = f"{INCIDENT_URL}/resolution"
CLOSURE_URL = f"{INCIDENT_URL}/closure"


class RecordingAuthenticator:
    """返回固定管理员并记录 Bearer Token。"""

    def __init__(self, principal: AdministratorPrincipal) -> None:
        self.principal = principal
        self.tokens: list[str] = []

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        self.tokens.append(bearer_token)
        return self.principal


class RecordingResolutionService:
    """记录路由构造的解决命令。"""

    def __init__(self) -> None:
        self.commands: list[object] = []
        self.close_commands: list[object] = []
        self.queries: list[object] = []
        self.list_queries: list[object] = []

    async def resolve(self, command) -> IncidentResolutionResult:
        self.commands.append(command)
        return IncidentResolutionResult(
            incident_id=command.incident_id,
            tenant_id=command.tenant_id,
            status="RESOLVED",
            version=command.expected_version + 1,
            resolved_by=command.requested_by,
            resolution_reason=command.reason,
            resolved_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
            trace_id=command.trace_id,
            is_duplicate=False,
        )

    async def close(self, command) -> IncidentClosureResult:
        self.close_commands.append(command)
        return IncidentClosureResult(
            incident_id=command.incident_id,
            tenant_id=command.tenant_id,
            status="CLOSED",
            version=command.expected_version + 1,
            closed_by=command.requested_by,
            closure_reason=command.reason,
            closed_at=datetime(2026, 7, 2, 11, 0, tzinfo=UTC),
            trace_id=command.trace_id,
            is_duplicate=False,
        )

    async def get(self, query) -> IncidentView:
        self.queries.append(query)
        return self._view(query.tenant_id, query.incident_id)

    @staticmethod
    def _view(tenant_id: str, incident_id: str) -> IncidentView:
        """构造查询接口返回的固定事故视图。"""
        return IncidentView(
            incident_id=incident_id,
            tenant_id=tenant_id,
            service_name="checkout-api",
            severity="CRITICAL",
            status="RESOLVED",
            title="Checkout outage",
            created_at=datetime(2026, 7, 2, 9, 0, tzinfo=UTC),
            updated_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
            version=2,
            resolved_by="admin_001",
            resolution_reason="Mitigation verified.",
            resolved_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
        )

    async def list(self, query) -> IncidentListView:
        self.list_queries.append(query)
        return IncidentListView(
            items=(self._view(query.tenant_id, "inc_001"),),
            next_cursor="next_cursor_001",
        )


def build_principal(
    *,
    scopes: frozenset[str] = frozenset({"incidents:resolve"}),
    tenant_ids: frozenset[str] = frozenset({"tenant_001"}),
) -> AdministratorPrincipal:
    """构造事故解决管理员。"""
    return AdministratorPrincipal(
        admin_id="admin_001",
        scopes=scopes,
        tenant_ids=tenant_ids,
    )


def build_client(
    principal: AdministratorPrincipal | None = None,
) -> tuple[TestClient, RecordingResolutionService]:
    """装配不启动数据库的事故解决接口。"""
    authenticator = RecordingAuthenticator(principal or build_principal())
    service = RecordingResolutionService()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=authenticator,
    )
    app.state.incident_resolution_service = service
    app.state.incident_query_service = service
    return TestClient(app), service


def valid_headers(if_match: str = '"1"') -> dict[str, str]:
    """构造认证、幂等和条件更新请求头。"""
    return {
        "Authorization": "Bearer token_001",
        "Idempotency-Key": "idem_resolution_001",
        "If-Match": if_match,
        "X-Trace-Id": "trc_resolution_http_001",
    }


def valid_closure_headers(if_match: str = '"2"') -> dict[str, str]:
    """构造关闭请求使用的认证、幂等和条件更新请求头。"""
    return {
        "Authorization": "Bearer token_001",
        "Idempotency-Key": "idem_closure_001",
        "If-Match": if_match,
        "X-Trace-Id": "trc_closure_http_001",
    }


def test_resolve_builds_authenticated_command_and_returns_etag() -> None:
    """路由应组合可信管理员、路径、正文和条件头。"""
    client, service = build_client()

    response = client.post(
        URL,
        json={"reason": "Mitigation verified."},
        headers=valid_headers(),
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"2"'
    assert response.headers["X-Trace-Id"] == "trc_resolution_http_001"
    assert response.json()["data"]["status"] == "RESOLVED"
    command = service.commands[0]
    assert command.tenant_id == "tenant_001"
    assert command.incident_id == "inc_001"
    assert command.expected_version == 1
    assert command.idempotency_key == "idem_resolution_001"
    assert command.requested_by == "admin_001"
    assert command.reason == "Mitigation verified."


def test_get_incident_uses_read_scope_and_returns_current_etag() -> None:
    """读接口不需要写请求头，并返回后续条件更新所需版本。"""
    principal = build_principal(
        scopes=frozenset({"incidents:read", "incidents:resolve"})
    )
    client, service = build_client(principal)

    response = client.get(
        INCIDENT_URL,
        headers={
            "Authorization": "Bearer token_001",
            "X-Trace-Id": "trc_incident_read_001",
        },
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"2"'
    assert response.json()["data"]["status"] == "RESOLVED"
    assert response.json()["data"]["resolution_reason"] == (
        "Mitigation verified."
    )
    query = service.queries[0]
    assert query.tenant_id == "tenant_001"
    assert query.incident_id == "inc_001"


def test_get_incident_requires_read_scope_and_tenant_access() -> None:
    """resolve 权限不能替代 read 权限，跨租户读取继续失败关闭。"""
    cases = (
        build_principal(scopes=frozenset({"incidents:resolve"})),
        build_principal(
            scopes=frozenset({"incidents:read"}),
            tenant_ids=frozenset({"tenant_other"}),
        ),
    )
    for principal in cases:
        client, service = build_client(principal)

        response = client.get(
            INCIDENT_URL,
            headers={"Authorization": "Bearer token_001"},
        )

        assert response.status_code == 403
        assert service.queries == []


def test_list_incidents_accepts_status_filters_and_limit() -> None:
    """列表路由应把重复状态参数转换成有界应用查询。"""
    client, service = build_client(
        build_principal(scopes=frozenset({"incidents:read"}))
    )

    response = client.get(
        f"{INCIDENT_URL.rsplit('/', 1)[0]}",
        params=[
            ("status", "OPEN"),
            ("status", "ANALYZING"),
            ("limit", "2"),
        ],
        headers={"Authorization": "Bearer token_001"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["next_cursor"] == "next_cursor_001"
    assert len(response.json()["data"]["items"]) == 1
    query = service.list_queries[0]
    assert {status.value for status in query.statuses} == {
        "OPEN",
        "ANALYZING",
    }
    assert query.limit == 2


def test_list_incidents_rejects_invalid_status_and_limit() -> None:
    """未知状态、越界容量和脏游标不能进入查询服务。"""
    client, service = build_client(
        build_principal(scopes=frozenset({"incidents:read"}))
    )
    list_url = INCIDENT_URL.rsplit("/", 1)[0]

    invalid_status = client.get(
        list_url,
        params={"status": "UNKNOWN"},
        headers={"Authorization": "Bearer token_001"},
    )
    invalid_limit = client.get(
        list_url,
        params={"limit": "101"},
        headers={"Authorization": "Bearer token_001"},
    )
    dirty_cursor = client.get(
        list_url,
        params={"cursor": "abc\x7f"},
        headers={"Authorization": "Bearer token_001"},
    )

    assert invalid_status.status_code == 422
    assert invalid_limit.status_code == 422
    assert dirty_cursor.status_code == 422
    assert service.list_queries == []


def test_scope_and_tenant_access_fail_closed() -> None:
    """管理员必须同时拥有 resolve Scope 和目标租户访问权。"""
    cases = (
        build_principal(scopes=frozenset({"incidents:read"})),
        build_principal(tenant_ids=frozenset({"tenant_other"})),
    )
    for principal in cases:
        client, service = build_client(principal)

        response = client.post(
            URL,
            json={"reason": "Mitigation verified."},
            headers=valid_headers(),
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        assert service.commands == []


def test_headers_etag_and_payload_are_strict() -> None:
    """缺失条件头、弱 ETag 和未知正文都不能进入应用服务。"""
    client, service = build_client()
    no_match = valid_headers()
    no_match.pop("If-Match")
    no_key = valid_headers()
    no_key.pop("Idempotency-Key")

    assert client.post(
        URL,
        json={"reason": "Mitigation verified."},
        headers=no_match,
    ).status_code == 428
    assert client.post(
        URL,
        json={"reason": "Mitigation verified."},
        headers=no_key,
    ).status_code == 422
    for value in ("1", 'W/"1"', '"0"', '"invalid"'):
        response = client.post(
            URL,
            json={"reason": "Mitigation verified."},
            headers=valid_headers(if_match=value),
        )
        assert response.status_code == 400
    assert client.post(
        URL,
        json={"reason": "ok", "status": "RESOLVED"},
        headers=valid_headers(),
    ).status_code == 422
    assert service.commands == []


def test_close_builds_authenticated_command_and_returns_etag() -> None:
    """关闭路由应组合可信管理员、路径、正文和条件头。"""
    client, service = build_client(
        build_principal(scopes=frozenset({"incidents:close"}))
    )

    response = client.post(
        CLOSURE_URL,
        json={"reason": "Post-incident checklist completed."},
        headers=valid_closure_headers(),
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"3"'
    assert response.headers["X-Trace-Id"] == "trc_closure_http_001"
    assert response.json()["data"]["status"] == "CLOSED"
    command = service.close_commands[0]
    assert command.tenant_id == "tenant_001"
    assert command.incident_id == "inc_001"
    assert command.expected_version == 2
    assert command.idempotency_key == "idem_closure_001"
    assert command.requested_by == "admin_001"
    assert command.reason == "Post-incident checklist completed."


def test_close_scope_and_tenant_access_fail_closed() -> None:
    """管理员必须同时拥有 close Scope 和目标租户访问权。"""
    cases = (
        build_principal(scopes=frozenset({"incidents:resolve"})),
        build_principal(
            scopes=frozenset({"incidents:close"}),
            tenant_ids=frozenset({"tenant_other"}),
        ),
    )
    for principal in cases:
        client, service = build_client(principal)

        response = client.post(
            CLOSURE_URL,
            json={"reason": "Post-incident checklist completed."},
            headers=valid_closure_headers(),
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        assert service.close_commands == []


def test_close_headers_etag_and_payload_are_strict() -> None:
    """关闭同样需要幂等键、强 ETag 和严格正文。"""
    client, service = build_client(
        build_principal(scopes=frozenset({"incidents:close"}))
    )
    no_match = valid_closure_headers()
    no_match.pop("If-Match")
    no_key = valid_closure_headers()
    no_key.pop("Idempotency-Key")

    assert client.post(
        CLOSURE_URL,
        json={"reason": "Post-incident checklist completed."},
        headers=no_match,
    ).status_code == 428
    assert client.post(
        CLOSURE_URL,
        json={"reason": "Post-incident checklist completed."},
        headers=no_key,
    ).status_code == 422
    for value in ("2", 'W/"2"', '"0"', '"invalid"'):
        response = client.post(
            CLOSURE_URL,
            json={"reason": "Post-incident checklist completed."},
            headers=valid_closure_headers(if_match=value),
        )
        assert response.status_code == 400
    assert client.post(
        CLOSURE_URL,
        json={"reason": "ok", "status": "CLOSED"},
        headers=valid_closure_headers(),
    ).status_code == 422
    assert service.close_commands == []


def test_missing_resolution_service_returns_503() -> None:
    """Runtime 未装配解决服务时不能伪造成功。"""
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=RecordingAuthenticator(build_principal()),
    )

    response = TestClient(app).post(
        URL,
        json={"reason": "Mitigation verified."},
        headers=valid_headers(),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"


def test_missing_closure_service_returns_503() -> None:
    """Runtime 未装配解决/关闭服务时不能伪造关闭成功。"""
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=RecordingAuthenticator(
            build_principal(scopes=frozenset({"incidents:close"}))
        ),
    )

    response = TestClient(app).post(
        CLOSURE_URL,
        json={"reason": "Post-incident checklist completed."},
        headers=valid_closure_headers(),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"


def test_missing_query_service_returns_503() -> None:
    """Runtime 未装配查询服务时读取接口必须失败关闭。"""
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=RecordingAuthenticator(
            build_principal(scopes=frozenset({"incidents:read"}))
        ),
    )

    response = TestClient(app).get(
        INCIDENT_URL,
        headers={"Authorization": "Bearer token_001"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"
