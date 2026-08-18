from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.workspace_service import (
    WorkspaceChangeResult,
)
from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.domain.models.workspace import WorkspaceConfig

BASE_URL = "/api/v1/admin/tenants/tenant-a/workspaces/shop"


class Authenticator:
    def __init__(self, principal: AdministratorPrincipal) -> None:
        self.principal = principal

    async def authenticate(self, bearer_token: str) -> AdministratorPrincipal:
        assert bearer_token == "workspace-token"
        return self.principal


class RecordingWorkspaceService:
    def __init__(self) -> None:
        self.commands = []
        self.workspace = WorkspaceConfig(
            workspace_id="shop",
            tenant_id="tenant-a",
            name="MiniShop",
            prometheus_target="https://prometheus.example",
            loki_target="https://loki.example",
            tempo_target="https://tempo.example",
            revision=1,
            updated_at=datetime(2026, 8, 18, tzinfo=UTC),
        )

    async def upsert(self, command) -> WorkspaceChangeResult:
        self.commands.append(command)
        return WorkspaceChangeResult(
            operation_id="wop-001",
            workspace=self.workspace,
            trace_id=command.trace_id,
            is_duplicate=False,
        )

    async def get(self, tenant_id: str, workspace_id: str) -> WorkspaceConfig:
        assert (tenant_id, workspace_id) == ("tenant-a", "shop")
        return self.workspace

    async def list(self, tenant_id: str) -> tuple[WorkspaceConfig, ...]:
        assert tenant_id == "tenant-a"
        return (self.workspace,)


def principal(scopes: frozenset[str]) -> AdministratorPrincipal:
    return AdministratorPrincipal(
        admin_id="admin-001",
        scopes=scopes,
        tenant_ids=frozenset({"tenant-a"}),
    )


def client(scopes: frozenset[str]) -> tuple[TestClient, RecordingWorkspaceService]:
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=Authenticator(principal(scopes)),
    )
    service = RecordingWorkspaceService()
    app.state.workspace_service = service
    return TestClient(app), service


def headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer workspace-token",
        "Idempotency-Key": "workspace-idem-001",
        "If-Match": '"0"',
        "X-Trace-Id": "trace-workspace-http",
    }


def payload() -> dict:
    return {
        "name": "MiniShop",
        "prometheus_target": "https://prometheus.example",
        "loki_target": "https://loki.example",
        "tempo_target": "https://tempo.example",
        "knowledge_scope": "tenant",
        "investigation_policy": "bounded_dynamic_v1",
        "allowed_tools": ["metrics.query@v1"],
        "llm_provider_policy": "approved-primary",
        "retention_days": 30,
    }


def test_workspace_put_builds_trusted_command_and_etag() -> None:
    http, service = client(frozenset({"workspaces:write"}))

    response = http.put(BASE_URL, headers=headers(), json=payload())

    assert response.status_code == 200
    assert response.headers["ETag"] == '"1"'
    assert response.json()["data"]["revision"] == 1
    command = service.commands[0]
    assert command.tenant_id == "tenant-a"
    assert command.workspace_id == "shop"
    assert command.requested_by == "admin-001"
    assert command.expected_revision == 0


def test_workspace_get_and_list_require_read_scope() -> None:
    http, _ = client(frozenset({"workspaces:read"}))

    item = http.get(BASE_URL, headers={"Authorization": "Bearer workspace-token"})
    listing = http.get(
        "/api/v1/admin/tenants/tenant-a/workspaces",
        headers={"Authorization": "Bearer workspace-token"},
    )

    assert item.status_code == 200
    assert item.headers["ETag"] == '"1"'
    assert listing.status_code == 200
    assert len(listing.json()["data"]["items"]) == 1


def test_workspace_routes_fail_closed_on_scope_headers_and_extra_secret() -> None:
    http, service = client(frozenset({"workspaces:read"}))
    body = payload()
    body["api_key"] = "must-not-enter-command"

    denied = http.put(BASE_URL, headers=headers(), json=payload())
    invalid = http.put(BASE_URL, headers=headers(), json=body)
    no_match_headers = headers()
    no_match_headers.pop("If-Match")
    missing_match = http.put(BASE_URL, headers=no_match_headers, json=payload())

    assert denied.status_code == 403
    assert invalid.status_code == 422
    assert missing_match.status_code == 428
    assert service.commands == []
