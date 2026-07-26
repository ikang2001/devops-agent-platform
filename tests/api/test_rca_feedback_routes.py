from datetime import UTC, datetime

from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.rca_feedback_service import (
    RCAFeedbackView,
)
from devops_agent_platform.bootstrap.app import create_app

URL = "/api/v1/admin/tenants/tenant_001/workflow-runs/wfr_001/feedback"
NOW = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)


class StaticAuthenticator:
    def __init__(self, principal: AdministratorPrincipal) -> None:
        self.principal = principal

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        assert bearer_token == "admin-token"
        return self.principal


class RecordingFeedbackService:
    def __init__(self) -> None:
        self.commands: list[object] = []
        self.queries: list[object] = []

    async def create(self, command) -> RCAFeedbackView:
        self.commands.append(command)
        return feedback_view()

    async def list(self, query) -> tuple[RCAFeedbackView, ...]:
        self.queries.append(query)
        return (feedback_view(),)


def feedback_view() -> RCAFeedbackView:
    return RCAFeedbackView(
        feedback_id="rcf_001",
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        report_id="rpt_001",
        verdict="PARTIAL",
        corrected_root_cause="Inventory pool exhaustion.",
        missing_evidence_types=("DEPLOYMENT",),
        unsafe_recommendation_indexes=(1,),
        follow_up_label="needs-runbook",
        notes=None,
        created_by="admin_001",
        trace_id="trc_feedback_001",
        created_at=NOW,
    )


def build_client(
    scopes: frozenset[str],
) -> tuple[TestClient, RecordingFeedbackService]:
    principal = AdministratorPrincipal(
        admin_id="admin_001",
        scopes=scopes,
        tenant_ids=frozenset({"tenant_001"}),
    )
    service = RecordingFeedbackService()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=StaticAuthenticator(principal),
    )
    app.state.rca_feedback_service = service
    return TestClient(app), service


def test_create_feedback_uses_authenticated_identity_and_strict_payload() -> None:
    client, service = build_client(frozenset({"rca_feedback:write"}))

    response = client.post(
        URL,
        headers={
            "Authorization": "Bearer admin-token",
            "Idempotency-Key": "feedback-key-001",
            "X-Trace-Id": "trc_feedback_001",
        },
        json={
            "verdict": "PARTIAL",
            "corrected_root_cause": "Inventory pool exhaustion.",
            "missing_evidence_types": ["DEPLOYMENT"],
            "unsafe_recommendation_indexes": [1],
            "follow_up_label": "needs-runbook",
            "notes": None,
        },
    )

    assert response.status_code == 201
    assert response.json()["data"]["feedback_id"] == "rcf_001"
    command = service.commands[0]
    assert command.requested_by == "admin_001"
    assert command.idempotency_key == "feedback-key-001"
    assert command.missing_evidence_types[0].value == "DEPLOYMENT"


def test_list_feedback_is_bounded_and_tenant_scoped() -> None:
    client, service = build_client(frozenset({"rca_feedback:read"}))

    response = client.get(
        URL,
        params={"limit": 10},
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["verdict"] == "PARTIAL"
    assert service.queries[0].limit == 10
    assert service.queries[0].tenant_id == "tenant_001"


def test_feedback_routes_require_dedicated_scopes_and_valid_indexes() -> None:
    client, service = build_client(frozenset({"rca:read"}))
    headers = {
        "Authorization": "Bearer admin-token",
        "Idempotency-Key": "feedback-key-001",
    }

    forbidden = client.post(
        URL,
        headers=headers,
        json={"verdict": "ACCEPTED"},
    )
    invalid = client.post(
        URL,
        headers=headers,
        json={
            "verdict": "PARTIAL",
            "unsafe_recommendation_indexes": [2, 1],
        },
    )

    assert forbidden.status_code == 403
    assert invalid.status_code == 422
    assert service.commands == []
