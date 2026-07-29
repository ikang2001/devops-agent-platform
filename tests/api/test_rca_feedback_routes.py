from datetime import UTC, datetime

from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.rca_feedback_service import (
    EvaluationEvidenceView,
    RCAFeedbackEvaluationCandidateView,
    RCAFeedbackView,
)
from devops_agent_platform.bootstrap.app import create_app

URL = "/api/v1/admin/tenants/tenant_001/workflow-runs/wfr_001/feedback"
EXPORT_URL = f"{URL}/rcf_001/evaluation-candidate"
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

    async def get_evaluation_candidate(self, query):
        self.queries.append(query)
        return evaluation_candidate_view()


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


def evaluation_candidate_view() -> RCAFeedbackEvaluationCandidateView:
    return RCAFeedbackEvaluationCandidateView(
        schema_version=1,
        case_id="rca-feedback-rcf_001",
        workflow_run_id="wfr_001",
        incident_id="inc_001",
        report_id="rpt_001",
        feedback_id="rcf_001",
        review_required=True,
        baseline_generator_name="deterministic",
        baseline_generator_version="1",
        baseline_conclusion_status="CANDIDATE",
        baseline_title="Inventory timeout",
        baseline_summary="Inventory pool exhausted.",
        baseline_confidence=0.9,
        baseline_recommendations=("Restart inventory.",),
        verdict="PARTIAL",
        expected_root_cause="Inventory pool exhaustion.",
        required_evidence_ids=("evd_001",),
        missing_evidence_types=("TRACE",),
        unsafe_recommendation_indexes=(0,),
        follow_up_label="needs-runbook",
        evidence=(
            EvaluationEvidenceView(
                evidence_id="evd_001",
                evidence_type="METRIC",
                source="prometheus",
                summary="Pool saturation increased.",
            ),
        ),
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


def test_export_candidate_requires_dedicated_scope_and_returns_safe_shape() -> None:
    client, service = build_client(frozenset({"rca_feedback:export"}))

    response = client.get(
        EXPORT_URL,
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["review_required"] is True
    assert data["evidence"][0]["evidence_id"] == "evd_001"
    query = service.queries[0]
    assert query.tenant_id == "tenant_001"
    assert query.workflow_run_id == "wfr_001"
    assert query.feedback_id == "rcf_001"
    assert "notes" not in data
    assert "created_by" not in data
    assert "trace_id" not in data

    read_client, read_service = build_client(
        frozenset({"rca_feedback:read", "rca_feedback:write"})
    )
    forbidden = read_client.get(
        EXPORT_URL,
        headers={"Authorization": "Bearer admin-token"},
    )
    assert forbidden.status_code == 403
    assert read_service.queries == []


def test_export_candidate_rejects_dirty_feedback_path() -> None:
    client, service = build_client(frozenset({"rca_feedback:export"}))
    headers = {"Authorization": "Bearer admin-token"}

    dirty_control = client.get(
        f"{URL}/rcf_001%7F/evaluation-candidate",
        headers=headers,
    )
    dirty_space = client.get(
        f"{URL}/rcf%20001/evaluation-candidate",
        headers=headers,
    )

    for response in (dirty_control, dirty_space):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert service.queries == []
