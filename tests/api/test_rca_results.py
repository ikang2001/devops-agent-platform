from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.bootstrap.app import create_app

URL = (
    "/api/v1/admin/tenants/tenant_001/workflow-runs/"
    "wfr_001/result"
)
CANCELLATION_URL = (
    "/api/v1/admin/tenants/tenant_001/workflow-runs/"
    "wfr_001/cancellation"
)


class RecordingAuthenticator:
    """返回可配置管理员主体并记录 Bearer Token。"""

    def __init__(self, principal: AdministratorPrincipal) -> None:
        self.principal = principal
        self.tokens: list[str] = []

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        self.tokens.append(bearer_token)
        return self.principal


class StaticResult:
    """查询服务替身返回的最小可序列化结果。"""

    version = 3

    def to_dict(self) -> dict:
        return {
            "workflow_run_id": "wfr_001",
            "status": "SUCCEEDED",
            "version": self.version,
            "evidence": [],
            "invocations": [],
        }


class StaticCancellationResult:
    """取消服务替身返回的最小可序列化结果。"""

    version = 4

    def to_dict(self) -> dict:
        return {
            "workflow_run_id": "wfr_001",
            "status": "CANCELED",
            "version": self.version,
            "canceled_by": "admin_001",
            "cancellation_reason": "Operator canceled stale RCA.",
        }


class RecordingQueryService:
    """记录路由构造的 Query 对象。"""

    def __init__(self) -> None:
        self.queries: list[object] = []
        self.cancel_commands: list[object] = []

    async def get_result(self, query) -> StaticResult:
        self.queries.append(query)
        return StaticResult()

    async def cancel(self, command) -> StaticCancellationResult:
        self.cancel_commands.append(command)
        return StaticCancellationResult()


def build_principal(
    *,
    scopes: frozenset[str] = frozenset({"rca:read"}),
    tenant_ids: frozenset[str] = frozenset({"tenant_001"}),
) -> AdministratorPrincipal:
    """构造具有目标租户读取权限的管理员。"""
    return AdministratorPrincipal(
        admin_id="admin_001",
        scopes=scopes,
        tenant_ids=tenant_ids,
    )


def build_client(
    principal: AdministratorPrincipal | None = None,
) -> tuple[TestClient, RecordingAuthenticator, RecordingQueryService]:
    """装配不启动真实数据库的查询接口测试应用。"""
    authenticator = RecordingAuthenticator(principal or build_principal())
    service = RecordingQueryService()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=authenticator,
    )
    app.state.rca_query_service = service
    app.state.rca_cancellation_service = service
    return TestClient(app), authenticator, service


def test_rca_result_route_requires_scope_and_preserves_query_context() -> None:
    """授权成功后应把路径和容量参数转换为应用查询对象。"""
    client, authenticator, service = build_client()

    response = client.get(
        URL,
        params={"limit": 25},
        headers={
            "Authorization": "Bearer admin-token",
            "X-Trace-Id": "trc_query_001",
        },
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"3"'
    body = response.json()
    assert body["success"] is True
    assert body["trace_id"] == "trc_query_001"
    assert body["data"]["workflow_run_id"] == "wfr_001"
    assert authenticator.tokens == ["admin-token"]
    assert service.queries[0].tenant_id == "tenant_001"
    assert service.queries[0].workflow_run_id == "wfr_001"
    assert service.queries[0].limit == 25


def test_cancel_rca_workflow_builds_authenticated_command_and_etag() -> None:
    """取消路由应组合可信管理员、路径、正文和条件头。"""
    client, _, service = build_client(
        build_principal(scopes=frozenset({"rca:cancel"}))
    )

    response = client.post(
        CANCELLATION_URL,
        json={"reason": "Operator canceled stale RCA."},
        headers={
            "Authorization": "Bearer admin-token",
            "Idempotency-Key": "idem_cancel_001",
            "If-Match": '"3"',
            "X-Trace-Id": "trc_cancel_http_001",
        },
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"4"'
    assert response.headers["X-Trace-Id"] == "trc_cancel_http_001"
    assert response.json()["data"]["status"] == "CANCELED"
    command = service.cancel_commands[0]
    assert command.tenant_id == "tenant_001"
    assert command.workflow_run_id == "wfr_001"
    assert command.expected_version == 3
    assert command.idempotency_key == "idem_cancel_001"
    assert command.requested_by == "admin_001"
    assert command.reason == "Operator canceled stale RCA."


def test_cancel_rca_workflow_scope_and_tenant_access_fail_closed() -> None:
    """取消需要独立 scope 和目标租户访问权。"""
    cases = (
        build_principal(scopes=frozenset({"rca:read"})),
        build_principal(
            scopes=frozenset({"rca:cancel"}),
            tenant_ids=frozenset({"tenant_other"}),
        ),
    )
    for principal in cases:
        client, _, service = build_client(principal)

        response = client.post(
            CANCELLATION_URL,
            json={"reason": "Operator canceled stale RCA."},
            headers={
                "Authorization": "Bearer admin-token",
                "Idempotency-Key": "idem_cancel_001",
                "If-Match": '"3"',
            },
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        assert service.cancel_commands == []


def test_cancel_rca_workflow_headers_etag_and_payload_are_strict() -> None:
    """取消同样需要幂等键、强 ETag 和严格正文。"""
    client, _, service = build_client(
        build_principal(scopes=frozenset({"rca:cancel"}))
    )
    base_headers = {
        "Authorization": "Bearer admin-token",
        "Idempotency-Key": "idem_cancel_001",
        "If-Match": '"3"',
    }
    no_match = dict(base_headers)
    no_match.pop("If-Match")
    no_key = dict(base_headers)
    no_key.pop("Idempotency-Key")

    assert client.post(
        CANCELLATION_URL,
        json={"reason": "Operator canceled stale RCA."},
        headers=no_match,
    ).status_code == 428
    assert client.post(
        CANCELLATION_URL,
        json={"reason": "Operator canceled stale RCA."},
        headers=no_key,
    ).status_code == 422
    for value in ("3", 'W/"3"', '"0"', '"invalid"'):
        headers = dict(base_headers)
        headers["If-Match"] = value
        response = client.post(
            CANCELLATION_URL,
            json={"reason": "Operator canceled stale RCA."},
            headers=headers,
        )
        assert response.status_code == 400
    assert client.post(
        CANCELLATION_URL,
        json={"reason": "ok", "status": "CANCELED"},
        headers=base_headers,
    ).status_code == 422
    assert service.cancel_commands == []


def test_rca_result_route_rejects_cross_tenant_access() -> None:
    """没有目标租户授权时不得进入应用查询服务。"""
    client, _, service = build_client(
        build_principal(tenant_ids=frozenset({"tenant_other"}))
    )

    response = client.get(
        URL,
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert service.queries == []


def test_rca_result_route_requires_bearer_token() -> None:
    """缺少认证头时返回统一 401 envelope。"""
    client, _, service = build_client()

    response = client.get(URL)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert service.queries == []


def test_rca_result_route_rejects_unbounded_limit() -> None:
    """HTTP 层先拒绝超出上限的读取请求。"""
    client, _, service = build_client()

    response = client.get(
        URL,
        params={"limit": 101},
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 422
    assert service.queries == []
