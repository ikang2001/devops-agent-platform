from fastapi.testclient import TestClient

from devops_agent_platform.application.commands.rca import StartRCACommand
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.rca_service import StartRCAResult
from devops_agent_platform.bootstrap.app import create_app

URL = (
    "/api/v1/admin/tenants/tenant-api/incidents/inc-api-001/rca"
)


class RecordingAuthenticator:
    """返回固定管理员并记录认证 Token。"""

    def __init__(self, principal: AdministratorPrincipal) -> None:
        self.principal = principal
        self.tokens: list[str] = []

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        self.tokens.append(bearer_token)
        return self.principal


class RecordingRCAService:
    """记录接口层传入的Command，并返回可预测的应用结果。"""

    def __init__(self) -> None:
        self.command: StartRCACommand | None = None

    async def start_rca(self, command: StartRCACommand) -> StartRCAResult:
        """模拟已实现应用服务，测试范围不触碰数据库。"""
        self.command = command
        return StartRCAResult(
            workflow_run_id="wfr_api_001",
            incident_id=command.incident_id,
            status="PENDING",
            trace_id=command.trace_id,
            is_duplicate=False,
        )


def build_principal(
    *,
    scopes: frozenset[str] = frozenset({"incidents:rca"}),
    tenant_ids: frozenset[str] = frozenset({"tenant-api"}),
) -> AdministratorPrincipal:
    """构造具有 RCA 启动权限的管理员。"""
    return AdministratorPrincipal(
        admin_id="admin-api",
        scopes=scopes,
        tenant_ids=tenant_ids,
    )


def build_client(
    principal: AdministratorPrincipal | None = None,
) -> tuple[TestClient, RecordingAuthenticator, RecordingRCAService]:
    """装配认证器和不访问数据库的 RCA 服务。"""
    authenticator = RecordingAuthenticator(principal or build_principal())
    service = RecordingRCAService()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=authenticator,
    )
    app.state.rca_application_service = service
    return TestClient(app), authenticator, service


def valid_headers() -> dict[str, str]:
    """构造认证、幂等和 trace 请求头。"""
    return {
        "Authorization": "Bearer token-api-001",
        "Idempotency-Key": "idem-api-001",
        "X-Trace-Id": "trc-api-001",
    }


def test_rca_route_accepts_request_and_preserves_context() -> None:
    """认证路由应返回202，并从可信主体注入操作者身份。"""
    client, authenticator, service = build_client()

    response = client.post(
        URL,
        headers=valid_headers(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["data"]["workflow_run_id"] == "wfr_api_001"
    assert body["data"]["status"] == "PENDING"
    assert body["trace_id"] == "trc-api-001"
    assert response.headers["X-Trace-Id"] == "trc-api-001"
    assert authenticator.tokens == ["token-api-001"]
    assert service.command is not None
    assert service.command.incident_id == "inc-api-001"
    assert service.command.tenant_id == "tenant-api"
    assert service.command.operator_id == "admin-api"
    assert service.command.idempotency_key == "idem-api-001"
    assert service.command.trace_id == "trc-api-001"


def test_rca_route_rejects_control_character_path_identity() -> None:
    """路径身份字段含不可见控制字符时不能进入应用服务。"""
    client, _, service = build_client()

    dirty_control = client.post(
        "/api/v1/admin/tenants/tenant-api/incidents/inc-api-001%7F/rca",
        headers=valid_headers(),
    )
    dirty_space = client.post(
        "/api/v1/admin/tenants/tenant-api/incidents/inc-api%20001/rca",
        headers=valid_headers(),
    )

    for response in (dirty_control, dirty_space):
        assert response.status_code == 422
        assert (
            response.json()["error"]["code"]
            == "REQUEST_VALIDATION_ERROR"
        )
    assert service.command is None


def test_rca_route_requires_idempotency_key() -> None:
    """缺少幂等键时应在接口边界拒绝，不能进入应用服务。"""
    client, _, service = build_client()

    response = client.post(
        URL,
        headers={"Authorization": "Bearer token-api-001"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert service.command is None


def test_rca_route_requires_bearer_authentication() -> None:
    """缺少 Bearer 身份时不得进入 RCA 应用服务。"""
    client, _, service = build_client()

    response = client.post(
        URL,
        headers={"Idempotency-Key": "idem-api-001"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert service.command is None


def test_rca_route_rejects_self_reported_identity_body() -> None:
    """新路由不得静默接受 tenant 或 operator 身份正文。"""
    client, _, service = build_client()

    response = client.post(
        URL,
        json={
            "tenant_id": "tenant-other",
            "operator_id": "spoofed-operator",
        },
        headers=valid_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert service.command is None


def test_rca_route_requires_scope_and_tenant_access() -> None:
    """RCA 启动必须同时满足 scope 和目标租户授权。"""
    cases = (
        build_principal(scopes=frozenset({"incidents:read"})),
        build_principal(tenant_ids=frozenset({"tenant-other"})),
    )
    for principal in cases:
        client, _, service = build_client(principal)

        response = client.post(URL, headers=valid_headers())

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        assert service.command is None


def test_legacy_anonymous_rca_route_is_not_available() -> None:
    """旧匿名入口必须移除，不能继续接受自报身份正文。"""
    client, _, service = build_client()

    response = client.post(
        "/api/v1/incidents/inc-api-001/rca",
        json={
            "tenant_id": "tenant-api",
            "operator_id": "spoofed-operator",
        },
        headers=valid_headers(),
    )

    assert response.status_code == 404
    assert service.command is None
