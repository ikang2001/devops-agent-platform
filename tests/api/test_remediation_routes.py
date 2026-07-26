from datetime import UTC, datetime

from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.remediation_service import (
    RemediationPlanView,
)
from devops_agent_platform.bootstrap.app import create_app

NOW = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)
CREATE_URL = (
    "/api/v1/admin/tenants/tenant_001/workflow-runs/wfr_001"
    "/remediation-plans"
)
PLAN_URL = "/api/v1/admin/tenants/tenant_001/remediation-plans/rmp_001"


class StaticAuthenticator:
    def __init__(self, principal: AdministratorPrincipal) -> None:
        self.principal = principal

    async def authenticate(self, bearer_token: str) -> AdministratorPrincipal:
        assert bearer_token == "admin-token"
        return self.principal


class RecordingService:
    def __init__(self) -> None:
        self.created = []
        self.decisions = []
        self.executions = []

    async def create(self, command):
        self.created.append(command)
        return plan_view()

    async def decide(self, command):
        self.decisions.append(command)
        return plan_view(status="APPROVED", version=2)

    async def execute(self, command):
        self.executions.append(command)
        return plan_view(status="SUCCEEDED", version=4)

    async def rollback(self, command):
        return plan_view(status="ROLLED_BACK", version=6)

    async def get(self, query):
        return plan_view()


def plan_view(
    *,
    status: str = "DRAFT",
    version: int = 1,
) -> RemediationPlanView:
    return RemediationPlanView(
        remediation_plan_id="rmp_001",
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        incident_id="inc_001",
        action_key="restart_inventory",
        target="inventory",
        expected_effect="Restore inventory availability.",
        risk="MEDIUM",
        rollback_action_key="restore_inventory_revision",
        evidence_ids=("evd_001", "evd_002", "evd_003", "evd_004"),
        dry_run_summary="Action and rollback are valid.",
        status=status,
        version=version,
        created_by="admin_author",
        created_at=NOW,
        decided_by=None,
        decision_reason=None,
        decided_at=None,
        execution_summary=None,
        execution_started_at=None,
        executed_at=None,
        executed_by=None,
        execution_attempt=0,
        execution_lease_expires_at=None,
        rollback_summary=None,
        rollback_started_at=None,
        rolled_back_at=None,
        rolled_back_by=None,
        rollback_attempt=0,
        rollback_lease_expires_at=None,
        trace_id="trc_remediation_001",
    )


def build_client(scopes: frozenset[str]):
    principal = AdministratorPrincipal(
        admin_id="admin_001",
        scopes=scopes,
        tenant_ids=frozenset({"tenant_001"}),
    )
    service = RecordingService()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=StaticAuthenticator(principal),
    )
    app.state.remediation_service = service
    return TestClient(app), service


def test_create_and_decide_use_identity_idempotency_and_etag() -> None:
    client, service = build_client(
        frozenset({"remediation:write", "remediation:approve"})
    )
    create = client.post(
        CREATE_URL,
        headers={
            "Authorization": "Bearer admin-token",
            "Idempotency-Key": "create-remediation-001",
        },
        json={
            "action_key": "restart_inventory",
            "target": "inventory",
            "evidence_ids": [
                "evd_001",
                "evd_002",
                "evd_003",
                "evd_004",
            ],
        },
    )
    decision = client.post(
        f"{PLAN_URL}/decision",
        headers={
            "Authorization": "Bearer admin-token",
            "Idempotency-Key": "approve-remediation-001",
            "If-Match": '"1"',
        },
        json={"approved": True, "reason": "Reviewed evidence."},
    )

    assert create.status_code == 200
    assert create.headers["etag"] == '"1"'
    assert decision.status_code == 200
    assert decision.headers["etag"] == '"2"'
    assert service.created[0].requested_by == "admin_001"
    assert service.decisions[0].expected_version == 1


def test_execute_requires_dedicated_scope_and_empty_body() -> None:
    forbidden_client, forbidden_service = build_client(
        frozenset({"remediation:read"})
    )
    headers = {
        "Authorization": "Bearer admin-token",
        "Idempotency-Key": "execute-remediation-001",
        "If-Match": '"2"',
    }
    forbidden = forbidden_client.post(
        f"{PLAN_URL}/execute",
        headers=headers,
        json={},
    )
    assert forbidden.status_code == 403
    assert forbidden_service.executions == []

    client, service = build_client(frozenset({"remediation:execute"}))
    invalid = client.post(
        f"{PLAN_URL}/execute",
        headers=headers,
        json={"command": "rm -rf /"},
    )
    accepted = client.post(
        f"{PLAN_URL}/execute",
        headers=headers,
        json={},
    )
    assert invalid.status_code == 422
    assert accepted.status_code == 200
    assert service.executions[0].remediation_plan_id == "rmp_001"
