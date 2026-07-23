from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.runbook_admin_service import (
    RunbookChangeResult,
)
from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.domain.exceptions import ConflictError

BASE_URL = (
    "/api/v1/admin/tenants/tenant_001/runbooks/"
    "checkout.error-rate/versions/v2"
)
DRAFT_URL = f"{BASE_URL}/draft"
PUBLISH_URL = f"{BASE_URL}/publish"


class RecordingAuthenticator:
    """返回可配置管理员并记录 Bearer Token。"""

    def __init__(
        self,
        principal: AdministratorPrincipal | None = None,
    ) -> None:
        self.principal = principal or build_principal()
        self.tokens: list[str] = []

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        self.tokens.append(bearer_token)
        return self.principal


class RecordingRunbookAdminService:
    """记录路由 Command 并返回稳定聚合修订号。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.draft_commands: list[object] = []
        self.publish_commands: list[object] = []

    async def save_draft(self, command) -> RunbookChangeResult:
        self.draft_commands.append(command)
        if self.error is not None:
            raise self.error
        return build_result(
            status="DRAFT",
            revision=command.expected_revision + 1,
            trace_id=command.trace_id,
        )

    async def publish(self, command) -> RunbookChangeResult:
        self.publish_commands.append(command)
        if self.error is not None:
            raise self.error
        return build_result(
            status="PUBLISHED",
            revision=command.expected_revision + 1,
            trace_id=command.trace_id,
        )


def build_principal(
    *,
    scopes: frozenset[str] = frozenset({"runbooks:write"}),
    tenant_ids: frozenset[str] = frozenset({"tenant_001"}),
) -> AdministratorPrincipal:
    """构造具有目标租户 Runbook 写权限的管理员。"""
    return AdministratorPrincipal(
        admin_id="admin_001",
        scopes=scopes,
        tenant_ids=tenant_ids,
    )


def build_result(
    *,
    status: str,
    revision: int,
    trace_id: str,
) -> RunbookChangeResult:
    """构造接口替身返回的管理结果。"""
    return RunbookChangeResult(
        operation_id="rop_001",
        runbook_id="rbk_001",
        tenant_id="tenant_001",
        runbook_key="checkout.error-rate",
        version="v2",
        status=status,
        revision=revision,
        trace_id=trace_id,
        is_duplicate=False,
    )


def build_client(
    *,
    authenticator: RecordingAuthenticator | None = None,
    service: RecordingRunbookAdminService | None = None,
) -> tuple[
    TestClient,
    RecordingAuthenticator,
    RecordingRunbookAdminService,
]:
    """装配不启动数据库的 Runbook 管理接口应用。"""
    resolved_authenticator = authenticator or RecordingAuthenticator()
    resolved_service = service or RecordingRunbookAdminService()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=resolved_authenticator,
    )
    app.state.runbook_admin_service = resolved_service
    return TestClient(app), resolved_authenticator, resolved_service


def valid_headers(if_match: str = '"2"') -> dict[str, str]:
    """构造认证、幂等和条件写请求头。"""
    return {
        "Authorization": "Bearer token_001",
        "Idempotency-Key": "idem_runbook_001",
        "If-Match": if_match,
        "X-Trace-Id": "trc_http_001",
    }


def valid_payload() -> dict:
    """构造有限、可审核的 Runbook 草稿正文。"""
    return {
        "service_name": "checkout-api",
        "title": "Checkout incident response",
        "summary": "Inspect dependencies before remediation.",
        "priority": 100,
        "steps": [
            "Check metrics, logs, and traces.",
            "Escalate before rollback.",
        ],
        "tags": ["checkout", "http-5xx"],
    }


def test_save_draft_builds_authenticated_command_and_etag() -> None:
    """草稿接口应组合路径、正文、管理员和并发请求头。"""
    client, authenticator, service = build_client()

    response = client.put(
        DRAFT_URL,
        json=valid_payload(),
        headers=valid_headers(),
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"3"'
    assert response.headers["X-Trace-Id"] == "trc_http_001"
    assert response.json()["data"]["status"] == "DRAFT"
    assert authenticator.tokens == ["token_001"]
    command = service.draft_commands[0]
    assert command.tenant_id == "tenant_001"
    assert command.runbook_key == "checkout.error-rate"
    assert command.version == "v2"
    assert command.expected_revision == 2
    assert command.idempotency_key == "idem_runbook_001"
    assert command.requested_by == "admin_001"
    assert command.steps == (
        "Check metrics, logs, and traces.",
        "Escalate before rollback.",
    )


def test_publish_uses_only_version_identity_and_revision() -> None:
    """发布接口应只引用已保存草稿，不接收正文替换。"""
    client, _, service = build_client()

    response = client.post(
        PUBLISH_URL,
        headers=valid_headers(if_match='"3"'),
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"4"'
    assert response.json()["data"]["status"] == "PUBLISHED"
    command = service.publish_commands[0]
    assert command.version == "v2"
    assert command.expected_revision == 3
    assert command.requested_by == "admin_001"


def test_missing_scope_and_cross_tenant_are_denied() -> None:
    """管理员必须同时拥有 Runbook 写 Scope 和目标租户访问权。"""
    cases = (
        build_principal(scopes=frozenset({"runbooks:read"})),
        build_principal(tenant_ids=frozenset({"tenant_other"})),
    )
    for principal in cases:
        service = RecordingRunbookAdminService()
        client, _, _ = build_client(
            authenticator=RecordingAuthenticator(principal),
            service=service,
        )

        response = client.put(
            DRAFT_URL,
            json=valid_payload(),
            headers=valid_headers(),
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        assert service.draft_commands == []


def test_conditional_and_idempotency_headers_are_required() -> None:
    """缺少并发或幂等条件时不得进入应用服务。"""
    client, _, service = build_client()
    no_match = valid_headers()
    no_match.pop("If-Match")
    no_key = valid_headers()
    no_key.pop("Idempotency-Key")

    missing_match = client.put(
        DRAFT_URL,
        json=valid_payload(),
        headers=no_match,
    )
    missing_key = client.put(
        DRAFT_URL,
        json=valid_payload(),
        headers=no_key,
    )

    assert missing_match.status_code == 428
    assert missing_key.status_code == 422
    assert service.draft_commands == []


def test_malformed_etag_and_publish_revision_zero_are_rejected() -> None:
    """弱 ETag、裸数字和不合法发布修订号不能进入 Store。"""
    client, _, service = build_client()

    for value in ("2", 'W/"2"', '"invalid"', '"0"'):
        response = client.post(
            PUBLISH_URL,
            headers=valid_headers(if_match=value),
        )
        assert response.status_code == 400

    assert service.publish_commands == []


def test_invalid_draft_payload_returns_unified_422() -> None:
    """未知字段和空步骤应在接口边界拒绝。"""
    client, _, service = build_client()
    payload = valid_payload()
    payload["steps"] = []
    payload["unexpected"] = True

    response = client.put(
        DRAFT_URL,
        json=payload,
        headers=valid_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert service.draft_commands == []


def test_application_conflict_keeps_unified_envelope() -> None:
    """聚合修订冲突应稳定映射为 409。"""
    client, _, _ = build_client(
        service=RecordingRunbookAdminService(
            ConflictError("Runbook revision conflict")
        )
    )

    response = client.put(
        DRAFT_URL,
        json=valid_payload(),
        headers=valid_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert response.json()["trace_id"] == "trc_http_001"


def test_missing_runtime_service_fails_closed() -> None:
    """服务未装配时管理员请求不能伪成功。"""
    authenticator = RecordingAuthenticator()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=authenticator,
    )

    response = TestClient(app).put(
        DRAFT_URL,
        json=valid_payload(),
        headers=valid_headers(),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"
