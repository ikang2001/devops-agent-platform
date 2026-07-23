from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.bootstrap.app import create_app


class RCAAuthenticator:
    """为 skeleton RCA 路由提供最小可信管理员。"""

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        assert bearer_token == "token-rca-001"
        return AdministratorPrincipal(
            admin_id="user-001",
            scopes=frozenset({"incidents:rca"}),
            tenant_ids=frozenset({"tenant-a"}),
        )


def test_alert_route_returns_not_implemented_envelope() -> None:
    client = TestClient(create_app(runtime_enabled=False))

    response = client.post(
        "/api/v1/alerts",
        json={
            "tenant_id": "tenant-a",
            "source": "alertmanager",
            "service_name": "checkout-api",
            "severity": "CRITICAL",
            "summary": "5xx error rate is high",
            "starts_at": "2026-06-27T10:00:00Z",
            "fingerprint": "fp-001",
            "external_event_id": "evt-001",
        },
    )

    assert response.status_code == 501
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_IMPLEMENTED"
    assert body["trace_id"] == response.headers["X-Trace-Id"]


def test_rca_route_returns_not_implemented_envelope() -> None:
    client = TestClient(
        create_app(
            runtime_enabled=False,
            admin_authenticator=RCAAuthenticator(),
        )
    )

    response = client.post(
        "/api/v1/admin/tenants/tenant-a/incidents/inc-001/rca",
        headers={
            "Authorization": "Bearer token-rca-001",
            "Idempotency-Key": "idem-rca-001",
        },
    )

    assert response.status_code == 501
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_IMPLEMENTED"
    assert body["trace_id"] == response.headers["X-Trace-Id"]
