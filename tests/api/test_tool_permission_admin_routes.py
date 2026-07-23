from datetime import datetime

from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.tool_permission_admin_service import (
    ToolPermissionChangeResult,
    ToolPermissionSnapshotView,
)
from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.domain.exceptions import (
    AuthenticationRequired,
    ConflictError,
)

URL = (
    "/api/v1/admin/tenants/tenant_001/operators/"
    "operator_001/tool-permissions"
)


class RecordingAuthenticator:
    """记录Bearer Token并返回可配置管理员主体。"""

    def __init__(
        self,
        principal: AdministratorPrincipal | None = None,
        error: Exception | None = None,
        invalid_result: object | None = None,
    ) -> None:
        self.principal = principal or build_principal()
        self.error = error
        self.invalid_result = invalid_result
        self.tokens: list[str] = []

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        self.tokens.append(bearer_token)
        if self.error is not None:
            raise self.error
        if self.invalid_result is not None:
            return self.invalid_result  # type: ignore[return-value]
        return self.principal


class RecordingPermissionAdminService:
    """记录路由生成的Command并返回稳定权限版本。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.get_queries: list[object] = []
        self.set_commands: list[object] = []
        self.revoke_commands: list[object] = []

    async def get_current(self, query) -> ToolPermissionSnapshotView:
        self.get_queries.append(query)
        if self.error is not None:
            raise self.error
        return ToolPermissionSnapshotView(
            tenant_id=query.tenant_id,
            operator_id=query.operator_id,
            grant_id="pgr_001",
            version=3,
            permission_tags=("logs:read", "tenant:observe"),
            expires_at=datetime.fromisoformat(
                "2026-06-30T12:00:00+00:00"
            ),
            active=True,
        )

    async def set_permissions(self, command) -> ToolPermissionChangeResult:
        self.set_commands.append(command)
        if self.error is not None:
            raise self.error
        return build_result(
            action="SET",
            version=command.expected_version + 1,
            trace_id=command.trace_id,
        )

    async def revoke_permissions(self, command) -> ToolPermissionChangeResult:
        self.revoke_commands.append(command)
        if self.error is not None:
            raise self.error
        return build_result(
            action="REVOKE",
            version=command.expected_version + 1,
            trace_id=command.trace_id,
        )


def build_principal(
    *,
    scopes: frozenset[str] = frozenset(
        {"tool_permissions:write"}
    ),
    tenant_ids: frozenset[str] = frozenset({"tenant_001"}),
) -> AdministratorPrincipal:
    """构造默认具有目标租户写权限的管理员主体。"""
    return AdministratorPrincipal(
        admin_id="admin_001",
        scopes=scopes,
        tenant_ids=tenant_ids,
    )


def build_result(
    *,
    action: str,
    version: int,
    trace_id: str,
) -> ToolPermissionChangeResult:
    """构造接口替身返回的权限变更结果。"""
    return ToolPermissionChangeResult(
        operation_id="pop_001",
        grant_id="pgr_001",
        tenant_id="tenant_001",
        operator_id="operator_001",
        action=action,
        version=version,
        trace_id=trace_id,
        is_duplicate=False,
    )


def build_client(
    *,
    authenticator: RecordingAuthenticator | None = None,
    service: RecordingPermissionAdminService | None = None,
) -> tuple[
    TestClient,
    RecordingAuthenticator,
    RecordingPermissionAdminService,
]:
    """装配不启动真实数据库的权限管理接口测试应用。"""
    resolved_authenticator = authenticator or RecordingAuthenticator()
    resolved_service = service or RecordingPermissionAdminService()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=resolved_authenticator,
    )
    app.state.tool_permission_admin_service = resolved_service
    return (
        TestClient(app),
        resolved_authenticator,
        resolved_service,
    )


def valid_headers(
    *,
    if_match: str = '"3"',
) -> dict[str, str]:
    """构造满足认证、幂等和条件写要求的请求头。"""
    return {
        "Authorization": "Bearer token_001",
        "Idempotency-Key": "idem_001",
        "If-Match": if_match,
        "X-Trace-Id": "trc_http_001",
    }


def valid_payload() -> dict:
    """构造完整替换权限的合法请求体。"""
    return {
        "permission_tags": ["logs:read", "tenant:observe"],
        "expires_at": "2026-06-30T12:00:00Z",
    }


def test_set_route_uses_authenticated_admin_and_version_headers() -> None:
    """PUT接口应从认证主体生成requested_by并返回新ETag。"""
    client, authenticator, service = build_client()

    response = client.put(
        URL,
        json=valid_payload(),
        headers=valid_headers(),
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"4"'
    assert response.headers["X-Trace-Id"] == "trc_http_001"
    body = response.json()
    assert body["success"] is True
    assert body["data"]["version"] == 4
    assert body["trace_id"] == "trc_http_001"
    assert authenticator.tokens == ["token_001"]

    command = service.set_commands[0]
    assert command.tenant_id == "tenant_001"
    assert command.operator_id == "operator_001"
    assert command.expected_version == 3
    assert command.idempotency_key == "idem_001"
    assert command.requested_by == "admin_001"
    assert command.trace_id == "trc_http_001"
    assert command.permission_tags == (
        "logs:read",
        "tenant:observe",
    )
    assert command.expires_at == datetime.fromisoformat(
        "2026-06-30T12:00:00+00:00"
    )


def test_get_route_uses_read_scope_and_returns_etag() -> None:
    """GET接口只读取权限快照，不要求幂等键或条件写头。"""
    client, authenticator, service = build_client(
        authenticator=RecordingAuthenticator(
            build_principal(scopes=frozenset({"tool_permissions:read"}))
        )
    )

    response = client.get(
        URL,
        headers={
            "Authorization": "Bearer token_001",
            "X-Trace-Id": "trc_http_001",
        },
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"3"'
    assert authenticator.tokens == ["token_001"]
    body = response.json()
    assert body["data"] == {
        "tenant_id": "tenant_001",
        "operator_id": "operator_001",
        "grant_id": "pgr_001",
        "version": 3,
        "permission_tags": ["logs:read", "tenant:observe"],
        "expires_at": "2026-06-30T12:00:00+00:00",
        "active": True,
    }
    query = service.get_queries[0]
    assert query.tenant_id == "tenant_001"
    assert query.operator_id == "operator_001"
    assert service.set_commands == []
    assert service.revoke_commands == []


def test_revoke_route_builds_authenticated_command() -> None:
    """DELETE接口应传递目标身份、幂等键和期望版本。"""
    client, _, service = build_client()

    response = client.delete(URL, headers=valid_headers(if_match='"5"'))

    assert response.status_code == 200
    assert response.headers["ETag"] == '"6"'
    command = service.revoke_commands[0]
    assert command.expected_version == 5
    assert command.requested_by == "admin_001"
    assert command.idempotency_key == "idem_001"


def test_missing_or_invalid_bearer_credentials_return_401() -> None:
    """缺少凭证和错误认证方案都应返回带挑战头的统一401。"""
    client, authenticator, service = build_client()

    missing = client.put(
        URL,
        json=valid_payload(),
        headers={
            "Idempotency-Key": "idem_001",
            "If-Match": '"0"',
        },
    )
    invalid = client.put(
        URL,
        json=valid_payload(),
        headers={
            **valid_headers(if_match='"0"'),
            "Authorization": "Basic credentials",
        },
    )
    dirty = client.put(
        URL,
        json=valid_payload(),
        headers={
            **valid_headers(if_match='"0"'),
            "Authorization": "Bearer token\x7f001",
        },
    )

    for response in (missing, invalid, dirty):
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert (
            response.json()["error"]["code"]
            == "AUTHENTICATION_REQUIRED"
        )
    assert authenticator.tokens == []
    assert service.set_commands == []


def test_rejected_token_returns_401_without_calling_service() -> None:
    """认证器拒绝Token时不得进入权限写服务。"""
    authenticator = RecordingAuthenticator(
        error=AuthenticationRequired("Token is invalid")
    )
    service = RecordingPermissionAdminService()
    client, _, _ = build_client(
        authenticator=authenticator,
        service=service,
    )

    response = client.put(
        URL,
        json=valid_payload(),
        headers=valid_headers(if_match='"0"'),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert service.set_commands == []


def test_unconfigured_or_broken_authenticator_returns_503() -> None:
    """认证器缺失、异常或返回错误主体时必须默认拒绝。"""
    service = RecordingPermissionAdminService()
    app = create_app(runtime_enabled=False)
    app.state.tool_permission_admin_service = service
    unconfigured = TestClient(app).put(
        URL,
        json=valid_payload(),
        headers=valid_headers(if_match='"0"'),
    )

    broken_client, _, _ = build_client(
        authenticator=RecordingAuthenticator(
            error=RuntimeError("oidc secret should not leak")
        ),
        service=service,
    )
    broken = broken_client.put(
        URL,
        json=valid_payload(),
        headers=valid_headers(if_match='"0"'),
    )
    invalid_client, _, _ = build_client(
        authenticator=RecordingAuthenticator(invalid_result={"sub": "x"}),
        service=service,
    )
    invalid = invalid_client.put(
        URL,
        json=valid_payload(),
        headers=valid_headers(if_match='"0"'),
    )

    for response in (unconfigured, broken, invalid):
        assert response.status_code == 503
    assert (
        broken.json()["error"]["code"]
        == "AUTHENTICATION_SERVICE_UNAVAILABLE"
    )
    assert "secret" not in broken.json()["error"]["message"]
    assert service.set_commands == []


def test_missing_scope_or_cross_tenant_principal_returns_403() -> None:
    """管理员必须同时拥有写scope和目标租户授权。"""
    no_scope_service = RecordingPermissionAdminService()
    no_scope_client, _, _ = build_client(
        authenticator=RecordingAuthenticator(
            build_principal(scopes=frozenset({"tool_permissions:read"}))
        ),
        service=no_scope_service,
    )
    cross_tenant_service = RecordingPermissionAdminService()
    cross_tenant_client, _, _ = build_client(
        authenticator=RecordingAuthenticator(
            build_principal(tenant_ids=frozenset({"tenant_other"}))
        ),
        service=cross_tenant_service,
    )

    no_scope = no_scope_client.put(
        URL,
        json=valid_payload(),
        headers=valid_headers(if_match='"0"'),
    )
    cross_tenant = cross_tenant_client.put(
        URL,
        json=valid_payload(),
        headers=valid_headers(if_match='"0"'),
    )

    for response in (no_scope, cross_tenant):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert no_scope_service.set_commands == []
    assert cross_tenant_service.set_commands == []


def test_get_route_requires_read_scope_and_tenant_access() -> None:
    """读取权限快照也必须通过scope和租户双重授权。"""
    no_scope_service = RecordingPermissionAdminService()
    no_scope_client, _, _ = build_client(
        authenticator=RecordingAuthenticator(
            build_principal(scopes=frozenset({"tool_permissions:write"}))
        ),
        service=no_scope_service,
    )
    cross_tenant_service = RecordingPermissionAdminService()
    cross_tenant_client, _, _ = build_client(
        authenticator=RecordingAuthenticator(
            build_principal(
                scopes=frozenset({"tool_permissions:read"}),
                tenant_ids=frozenset({"tenant_other"}),
            )
        ),
        service=cross_tenant_service,
    )

    no_scope = no_scope_client.get(
        URL,
        headers={"Authorization": "Bearer token_001"},
    )
    cross_tenant = cross_tenant_client.get(
        URL,
        headers={"Authorization": "Bearer token_001"},
    )

    for response in (no_scope, cross_tenant):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert no_scope_service.get_queries == []
    assert cross_tenant_service.get_queries == []


def test_missing_if_match_returns_428() -> None:
    """没有并发条件头时不得执行权限写操作。"""
    client, _, service = build_client()
    headers = valid_headers()
    headers.pop("If-Match")

    response = client.put(URL, json=valid_payload(), headers=headers)

    assert response.status_code == 428
    assert response.json()["error"]["code"] == "PRECONDITION_REQUIRED"
    assert service.set_commands == []


def test_malformed_if_match_returns_400() -> None:
    """弱ETag、未加引号和非数字版本都应拒绝。"""
    client, _, service = build_client()

    for value in ("3", 'W/"3"', '"invalid"'):
        response = client.put(
            URL,
            json=valid_payload(),
            headers=valid_headers(if_match=value),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    assert service.set_commands == []


def test_missing_idempotency_key_or_invalid_payload_returns_422() -> None:
    """接口传输契约缺失时由统一请求校验响应处理。"""
    client, _, service = build_client()
    headers = valid_headers(if_match='"0"')
    headers.pop("Idempotency-Key")
    dirty_headers = valid_headers(if_match='"0"')
    dirty_headers["Idempotency-Key"] = "idem\x7f001"
    spaced_headers = valid_headers(if_match='"0"')
    spaced_headers["Idempotency-Key"] = "idem 001"

    missing_key = client.put(
        URL,
        json=valid_payload(),
        headers=headers,
    )
    dirty_key = client.put(
        URL,
        json=valid_payload(),
        headers=dirty_headers,
    )
    spaced_key = client.put(
        URL,
        json=valid_payload(),
        headers=spaced_headers,
    )
    invalid_payload = client.put(
        URL,
        json={"permission_tags": [], "unexpected": True},
        headers=valid_headers(if_match='"0"'),
    )

    for response in (
        missing_key,
        dirty_key,
        spaced_key,
        invalid_payload,
    ):
        assert response.status_code == 422
        assert (
            response.json()["error"]["code"]
            == "REQUEST_VALIDATION_ERROR"
        )
    assert service.set_commands == []


def test_application_version_conflict_keeps_unified_envelope() -> None:
    """应用层乐观锁冲突应稳定映射为409。"""
    client, _, _ = build_client(
        service=RecordingPermissionAdminService(
            error=ConflictError("Tool permission version conflict")
        )
    )

    response = client.put(
        URL,
        json=valid_payload(),
        headers=valid_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert response.json()["trace_id"] == "trc_http_001"
