from datetime import UTC, datetime

from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.ticket_draft_service import (
    TicketDraftView,
)
from devops_agent_platform.application.services.ticket_submission_service import (
    TicketSubmissionView,
)
from devops_agent_platform.bootstrap.app import create_app

URL = (
    "/api/v1/admin/tenants/tenant_001/workflow-runs/"
    "wfr_001/ticket-draft"
)
DECISION_URL = f"{URL}/decision"
SUBMISSION_URL = f"{URL}/submissions"


class RecordingAuthenticator:
    """返回固定管理员身份。"""

    def __init__(self, principal: AdministratorPrincipal) -> None:
        self.principal = principal

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        assert bearer_token == "token_001"
        return self.principal


class RecordingTicketDraftService:
    """记录草稿与提交请求对象的接口替身。"""

    def __init__(self) -> None:
        self.create_commands: list[object] = []
        self.get_queries: list[object] = []
        self.decide_commands: list[object] = []
        self.submission_commands: list[object] = []
        self.list_submission_queries: list[object] = []

    async def create(self, command) -> TicketDraftView:
        self.create_commands.append(command)
        return build_view(trace_id=command.trace_id)

    async def get(self, query) -> TicketDraftView:
        self.get_queries.append(query)
        return build_view(trace_id="trc_created")

    async def decide(self, command) -> TicketDraftView:
        self.decide_commands.append(command)
        return build_view(
            trace_id=command.trace_id,
            status=(
                "APPROVED"
                if command.decision.value == "APPROVE"
                else "REJECTED"
            ),
            version=2,
            decided_by=command.requested_by,
            decision_reason=command.reason,
        )

    async def request_submission(self, command) -> TicketSubmissionView:
        self.submission_commands.append(command)
        return build_submission_view(
            trace_id=command.trace_id,
            target_system=command.target_system,
        )

    async def list_by_workflow(self, query) -> tuple[TicketSubmissionView, ...]:
        self.list_submission_queries.append(query)
        return (
            build_submission_view(
                trace_id="trc_submission_002",
                ticket_submission_id="tsb_002",
                target_system="servicenow",
                status="FAILED",
            ),
            build_submission_view(
                trace_id="trc_submission_001",
                ticket_submission_id="tsb_001",
                target_system="jira",
            ),
        )


def build_principal(
    scopes: frozenset[str],
    tenant_ids: frozenset[str] = frozenset({"tenant_001"}),
) -> AdministratorPrincipal:
    """构造指定权限范围的管理员。"""
    return AdministratorPrincipal(
        admin_id="admin_001",
        scopes=scopes,
        tenant_ids=tenant_ids,
    )


def build_view(
    trace_id: str,
    *,
    status: str = "DRAFT",
    version: int = 1,
    decided_by: str | None = None,
    decision_reason: str | None = None,
) -> TicketDraftView:
    """构造接口响应使用的安全视图。"""
    return TicketDraftView(
        ticket_draft_id="tdf_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        report_id="rpt_001",
        status=status,
        priority="P1",
        title="Checkout timeout",
        description="RCA conclusion: CANDIDATE",
        evidence_ids=("evd_001",),
        recommendations=("Review dependency.",),
        created_by="admin_001",
        trace_id=trace_id,
        created_at=datetime(2026, 7, 1, 16, 0, tzinfo=UTC),
        version=version,
        decided_by=decided_by,
        decision_reason=decision_reason,
        decided_at=(
            datetime(2026, 7, 1, 16, 5, tzinfo=UTC)
            if decided_by is not None
            else None
        ),
    )


def build_submission_view(
    trace_id: str,
    *,
    ticket_submission_id: str = "tsb_001",
    target_system: str = "jira",
    status: str = "REQUESTED",
) -> TicketSubmissionView:
    """构造提交接口响应使用的安全视图。"""
    return TicketSubmissionView(
        ticket_submission_id=ticket_submission_id,
        tenant_id="tenant_001",
        ticket_draft_id="tdf_001",
        workflow_run_id="wfr_001",
        target_system=target_system,
        status=status,
        requested_by="admin_001",
        trace_id=trace_id,
        requested_at=datetime(2026, 7, 1, 16, 10, tzinfo=UTC),
        version=2 if status != "REQUESTED" else 1,
        failure_reason=(
            "External ticketing provider rejected password=secret-token."
            if status == "FAILED"
            else None
        ),
    )


def build_client(
    scopes: frozenset[str],
    *,
    tenant_ids: frozenset[str] = frozenset({"tenant_001"}),
) -> tuple[TestClient, RecordingTicketDraftService]:
    """装配不启动真实 Runtime 的接口测试应用。"""
    service = RecordingTicketDraftService()
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=RecordingAuthenticator(
            build_principal(scopes, tenant_ids)
        ),
    )
    app.state.ticket_draft_service = service
    app.state.ticket_submission_service = service
    return TestClient(app), service


def headers(
    *,
    include_idempotency: bool = True,
    if_match: str | None = None,
) -> dict[str, str]:
    """构造认证、追踪和可选幂等请求头。"""
    values = {
        "Authorization": "Bearer token_001",
        "X-Trace-Id": "trc_http_001",
    }
    if include_idempotency:
        values["Idempotency-Key"] = "idem_ticket_001"
    if if_match is not None:
        values["If-Match"] = if_match
    return values


def test_create_builds_command_from_trusted_admin() -> None:
    """创建接口不接收正文，只组合可信路径、管理员和幂等键。"""
    client, service = build_client(
        frozenset({"ticket_drafts:write"})
    )

    response = client.post(URL, headers=headers())

    assert response.status_code == 200
    assert response.headers["ETag"] == '"1"'
    assert response.json()["data"]["priority"] == "P1"
    assert response.json()["trace_id"] == "trc_http_001"
    command = service.create_commands[0]
    assert command.tenant_id == "tenant_001"
    assert command.workflow_run_id == "wfr_001"
    assert command.requested_by == "admin_001"
    assert command.idempotency_key == "idem_ticket_001"


def test_get_uses_separate_read_scope() -> None:
    """读取接口只要求读权限且不要求幂等键。"""
    client, service = build_client(
        frozenset({"ticket_drafts:read"})
    )

    response = client.get(URL, headers=headers(include_idempotency=False))

    assert response.status_code == 200
    assert response.headers["ETag"] == '"1"'
    assert response.json()["data"]["ticket_draft_id"] == "tdf_001"
    query = service.get_queries[0]
    assert query.tenant_id == "tenant_001"
    assert query.workflow_run_id == "wfr_001"


def test_list_submissions_uses_read_scope_and_bounded_limit() -> None:
    """提交状态查询只要求读权限，不要求幂等键或条件版本。"""
    client, service = build_client(
        frozenset({"ticket_drafts:read"})
    )

    response = client.get(
        SUBMISSION_URL,
        params={"limit": 2},
        headers=headers(include_idempotency=False),
    )

    assert response.status_code == 200
    assert response.json()["trace_id"] == "trc_http_001"
    items = response.json()["data"]["items"]
    response_text = response.text
    assert [item["ticket_submission_id"] for item in items] == [
        "tsb_002",
        "tsb_001",
    ]
    assert items[0]["status"] == "FAILED"
    assert items[0]["failure_reason"] is None
    assert len(items[0]["failure_reason_sha256"]) == 64
    assert "password" not in response_text
    assert "secret-token" not in response_text
    query = service.list_submission_queries[0]
    assert query.tenant_id == "tenant_001"
    assert query.workflow_run_id == "wfr_001"
    assert query.limit == 2


def test_list_submissions_requires_read_scope_and_finite_limit() -> None:
    """没有读权限或请求无界读取时不得进入应用服务。"""
    no_scope_client, no_scope_service = build_client(
        frozenset({"ticket_drafts:submit"})
    )
    client, service = build_client(
        frozenset({"ticket_drafts:read"})
    )

    no_scope = no_scope_client.get(
        SUBMISSION_URL,
        headers=headers(include_idempotency=False),
    )
    invalid_limit = client.get(
        SUBMISSION_URL,
        params={"limit": 101},
        headers=headers(include_idempotency=False),
    )

    assert no_scope.status_code == 403
    assert invalid_limit.status_code == 422
    assert no_scope_service.list_submission_queries == []
    assert service.list_submission_queries == []


def test_write_and_read_scopes_are_not_interchangeable() -> None:
    """读写权限必须分别授权。"""
    read_client, read_service = build_client(
        frozenset({"ticket_drafts:read"})
    )
    write_client, write_service = build_client(
        frozenset({"ticket_drafts:write"})
    )

    create = read_client.post(URL, headers=headers())
    get = write_client.get(
        URL,
        headers=headers(include_idempotency=False),
    )

    assert create.status_code == 403
    assert get.status_code == 403
    assert read_service.create_commands == []
    assert write_service.get_queries == []


def test_cross_tenant_and_missing_idempotency_fail_closed() -> None:
    """越租户创建与缺少幂等键都不能进入应用服务。"""
    cross_client, cross_service = build_client(
        frozenset({"ticket_drafts:write"}),
        tenant_ids=frozenset({"tenant_other"}),
    )
    normal_client, normal_service = build_client(
        frozenset({"ticket_drafts:write"})
    )

    cross = cross_client.post(URL, headers=headers())
    missing_key = normal_client.post(
        URL,
        headers=headers(include_idempotency=False),
    )

    assert cross.status_code == 403
    assert missing_key.status_code == 422
    assert cross_service.create_commands == []
    assert normal_service.create_commands == []


def test_unconfigured_service_returns_503() -> None:
    """Runtime 未装配草稿服务时不能返回假成功。"""
    app = create_app(
        runtime_enabled=False,
        admin_authenticator=RecordingAuthenticator(
            build_principal(frozenset({"ticket_drafts:write"}))
        ),
    )

    response = TestClient(app).post(URL, headers=headers())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"


def test_approve_requires_dedicated_scope_and_strong_etag() -> None:
    """审批接口应构造带版本、幂等键和可信审批人的命令。"""
    client, service = build_client(
        frozenset({"ticket_drafts:approve"})
    )

    response = client.post(
        DECISION_URL,
        json={"decision": "APPROVE", "reason": None},
        headers=headers(if_match='"1"'),
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"2"'
    assert response.json()["data"]["status"] == "APPROVED"
    command = service.decide_commands[0]
    assert command.expected_version == 1
    assert command.requested_by == "admin_001"
    assert command.idempotency_key == "idem_ticket_001"
    assert command.reason is None


def test_reject_requires_reason() -> None:
    """拒绝必须携带有限原因，批准不得夹带原因。"""
    client, service = build_client(
        frozenset({"ticket_drafts:approve"})
    )

    missing_reason = client.post(
        DECISION_URL,
        json={"decision": "REJECT"},
        headers=headers(if_match='"1"'),
    )
    approval_with_reason = client.post(
        DECISION_URL,
        json={"decision": "APPROVE", "reason": "unexpected"},
        headers=headers(if_match='"1"'),
    )
    valid = client.post(
        DECISION_URL,
        json={
            "decision": "REJECT",
            "reason": "Evidence is insufficient.",
        },
        headers=headers(if_match='"1"'),
    )

    assert missing_reason.status_code == 400
    assert approval_with_reason.status_code == 400
    assert valid.status_code == 200
    assert valid.json()["data"]["status"] == "REJECTED"
    assert len(service.decide_commands) == 1


def test_decision_headers_and_scope_fail_closed() -> None:
    """缺少审批 Scope、If-Match 或幂等键时不得调用服务。"""
    no_scope_client, no_scope_service = build_client(
        frozenset({"ticket_drafts:write"})
    )
    client, service = build_client(
        frozenset({"ticket_drafts:approve"})
    )
    payload = {"decision": "APPROVE"}

    no_scope = no_scope_client.post(
        DECISION_URL,
        json=payload,
        headers=headers(if_match='"1"'),
    )
    missing_match = client.post(
        DECISION_URL,
        json=payload,
        headers=headers(),
    )
    missing_key_headers = headers(
        include_idempotency=False,
        if_match='"1"',
    )
    missing_key = client.post(
        DECISION_URL,
        json=payload,
        headers=missing_key_headers,
    )
    malformed = client.post(
        DECISION_URL,
        json=payload,
        headers=headers(if_match='W/"1"'),
    )

    assert no_scope.status_code == 403
    assert missing_match.status_code == 428
    assert missing_key.status_code == 422
    assert malformed.status_code == 400
    assert no_scope_service.decide_commands == []
    assert service.decide_commands == []


def test_submit_requires_dedicated_scope_and_approved_etag() -> None:
    """提交接口应构造带目标系统、版本和可信提交人的命令。"""
    client, service = build_client(
        frozenset({"ticket_drafts:submit"})
    )

    response = client.post(
        SUBMISSION_URL,
        json={"target_system": "jira"},
        headers=headers(if_match='"2"'),
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"1"'
    assert response.json()["data"]["status"] == "REQUESTED"
    assert response.json()["data"]["target_system"] == "jira"
    command = service.submission_commands[0]
    assert command.expected_draft_version == 2
    assert command.requested_by == "admin_001"
    assert command.idempotency_key == "idem_ticket_001"
    assert command.target_system == "jira"


def test_submit_headers_scope_and_target_fail_closed() -> None:
    """缺少提交 Scope、If-Match、幂等键或非法目标都不得调用服务。"""
    no_scope_client, no_scope_service = build_client(
        frozenset({"ticket_drafts:approve"})
    )
    client, service = build_client(
        frozenset({"ticket_drafts:submit"})
    )

    no_scope = no_scope_client.post(
        SUBMISSION_URL,
        json={"target_system": "jira"},
        headers=headers(if_match='"2"'),
    )
    missing_match = client.post(
        SUBMISSION_URL,
        json={"target_system": "jira"},
        headers=headers(),
    )
    missing_key = client.post(
        SUBMISSION_URL,
        json={"target_system": "jira"},
        headers=headers(
            include_idempotency=False,
            if_match='"2"',
        ),
    )
    invalid_target = client.post(
        SUBMISSION_URL,
        json={"target_system": "Jira"},
        headers=headers(if_match='"2"'),
    )

    assert no_scope.status_code == 403
    assert missing_match.status_code == 428
    assert missing_key.status_code == 422
    assert invalid_target.status_code == 422
    assert no_scope_service.submission_commands == []
    assert service.submission_commands == []
