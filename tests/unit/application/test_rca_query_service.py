from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType

import pytest

from devops_agent_platform.application.queries.rca_results import (
    GetRCAExecutionResultQuery,
)
from devops_agent_platform.application.services.rca_query_service import (
    RCAExecutionQueryService,
)
from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
    ToolInvocationStatus,
    ToolRiskLevel,
    WorkflowRunStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.domain.models.workflow_run import WorkflowRun

NOW = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)


class FakeWorkflowRepository:
    """按租户返回工作流，并记录查询参数。"""

    def __init__(self, workflow_run: WorkflowRun | None) -> None:
        self.workflow_run = workflow_run
        self.calls: list[tuple[str, str]] = []

    async def get_by_id(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> WorkflowRun | None:
        self.calls.append((tenant_id, workflow_run_id))
        if (
            self.workflow_run is not None
            and self.workflow_run.tenant_id == tenant_id
            and self.workflow_run.workflow_run_id == workflow_run_id
        ):
            return self.workflow_run
        return None


class FakeListRepository:
    """返回有限领域对象并记录服务层传入的读取上限。"""

    def __init__(self, items: list[object]) -> None:
        self.items = items
        self.calls: list[tuple[str, str, int]] = []

    async def list_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        limit: int,
    ) -> list:
        self.calls.append((tenant_id, workflow_run_id, limit))
        return self.items[:limit]


class FakeReportRepository:
    """返回可选报告并记录租户查询。"""

    def __init__(self, report: RCAReport | None) -> None:
        self.report = report
        self.calls: list[tuple[str, str]] = []

    async def get_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> RCAReport | None:
        self.calls.append((tenant_id, workflow_run_id))
        return self.report


class FakeUnitOfWork:
    """提供查询服务需要的三个只读仓储。"""

    def __init__(
        self,
        workflow_run: WorkflowRun | None,
        evidence: list[Evidence],
        invocations: list[ToolInvocation],
        report: RCAReport | None = None,
    ) -> None:
        self.workflow_runs = FakeWorkflowRepository(workflow_run)
        self.evidence = FakeListRepository(list(evidence))
        self.tool_invocations = FakeListRepository(list(invocations))
        self.rca_reports = FakeReportRepository(report)

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False


def build_workflow_run() -> WorkflowRun:
    """构造已经成功结束的工作流。"""
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="operator_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_001",
        status=WorkflowRunStatus.SUCCEEDED,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        ended_at=NOW,
        step_count=2,
        execution_attempts=1,
    )


def build_canceled_workflow_run() -> WorkflowRun:
    """构造带管理端取消事实的工作流。"""
    return WorkflowRun(
        workflow_run_id="wfr_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        operator_id="operator_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_001",
        status=WorkflowRunStatus.CANCELED,
        created_at=NOW,
        updated_at=NOW,
        started_at=None,
        ended_at=NOW,
        step_count=0,
        version=2,
        canceled_by="admin_001",
        cancellation_reason="Copied token=cancel-secret by mistake.",
        canceled_at=NOW,
        cancellation_idempotency_key_hash="c" * 64,
        cancellation_request_hash="d" * 64,
        cancellation_trace_id="trc_cancel_001",
    )


def build_evidence(index: int) -> Evidence:
    """构造包含敏感原始正文的证据，验证查询结果会主动排除它。"""
    content_json, content_sha256 = build_evidence_content(
        {"token": f"secret-{index}", "status": "ok"}
    )
    return Evidence(
        evidence_id=f"{index:064x}",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id=f"step-{index}",
        tool_name="logs.query",
        tool_version="v1",
        evidence_type=EvidenceType.LOG,
        source="loki",
        summary=f"evidence {index}",
        content_json=content_json,
        content_sha256=content_sha256,
        confidence=1.0,
        collected_at=NOW,
    )


def build_invocation(index: int) -> ToolInvocation:
    """构造工具调用审计记录。"""
    return ToolInvocation(
        invocation_id=f"{index + 100:064x}",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id=f"step-{index}",
        operator_id="operator_001",
        trace_id="trc_001",
        tool_name="logs.query",
        tool_version="v1",
        risk_level=ToolRiskLevel.LOW,
        status=ToolInvocationStatus.SUCCEEDED,
        input_summary="payload_fields=10",
        input_sha256="c" * 64,
        output_summary=f"invocation {index}",
        output_sha256="d" * 64,
        latency_ms=10,
        error_code=None,
        started_at=NOW,
        ended_at=NOW,
    )


def build_report() -> RCAReport:
    """构造查询结果中的确定性报告。"""
    return RCAReport(
        report_id="e" * 64,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.UNDETERMINED,
        title="Root cause requires human review",
        summary="No verified root cause candidate was produced.",
        confidence=0.0,
        evidence_ids=(f"{1:064x}",),
        evidence_type_counts=(("LOG", 1),),
        recommendations=("Review cited evidence.",),
        generator_name="deterministic-evidence-summary",
        generator_version="v1",
        generated_at=NOW,
    )


async def test_query_returns_bounded_sanitized_result() -> None:
    """服务多取一条判断截断，但只返回调用方允许的数量。"""
    unit_of_work = FakeUnitOfWork(
        build_workflow_run(),
        [build_evidence(1), build_evidence(2)],
        [build_invocation(1), build_invocation(2)],
        build_report(),
    )
    service = RCAExecutionQueryService(lambda: unit_of_work)

    result = await service.get_result(
        GetRCAExecutionResultQuery(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            limit=1,
        )
    )
    payload = result.to_dict()

    assert unit_of_work.evidence.calls[0][2] == 2
    assert unit_of_work.tool_invocations.calls[0][2] == 2
    assert len(payload["evidence"]) == 1
    assert len(payload["invocations"]) == 1
    assert payload["version"] == 1
    assert payload["evidence_truncated"] is True
    assert payload["invocations_truncated"] is True
    assert "content_json" not in payload["evidence"][0]
    assert "secret-1" not in str(payload)
    assert "lease_owner" not in payload
    assert "idempotency_key_hash" not in payload
    assert payload["report"]["conclusion_status"] == "UNDETERMINED"
    assert payload["report"]["confidence"] == 0.0


async def test_query_returns_redacted_cancellation_without_internal_hashes() -> None:
    """管理端读取取消事实，但不能看到取消幂等字段和敏感文本。"""
    unit_of_work = FakeUnitOfWork(
        build_canceled_workflow_run(),
        [],
        [],
    )
    service = RCAExecutionQueryService(lambda: unit_of_work)

    result = await service.get_result(
        GetRCAExecutionResultQuery(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
        )
    )
    payload = result.to_dict()

    assert payload["status"] == WorkflowRunStatus.CANCELED.value
    assert payload["version"] == 2
    assert payload["canceled_by"] == "admin_001"
    assert payload["cancellation_reason"] == "Copied token=[REDACTED] by mistake."
    assert payload["canceled_at"] == NOW.isoformat()
    assert "cancellation_idempotency_key_hash" not in payload
    assert "cancellation_request_hash" not in payload
    assert "cancellation_trace_id" not in payload
    assert "cancel-secret" not in str(payload)


async def test_query_redacts_historical_text_before_response() -> None:
    """查询接口要兜住历史脏数据和替代生成器产生的敏感摘要。"""
    evidence = replace(
        build_evidence(1),
        source="loki token=source-secret",
        summary="authorization=Bearer summary-secret needs review.",
    )
    object.__setattr__(
        evidence,
        "summary",
        "authorization=Bearer summary-secret needs\x7freview.",
    )
    invocation = replace(
        build_invocation(1),
        input_summary="payload password=input-secret",
        output_summary="tool token=output-secret returned rows",
    )
    object.__setattr__(
        invocation,
        "output_summary",
        "tool token=output-secret returned\x7frows",
    )
    report = replace(
        build_report(),
        title="Database password=report-secret",
        summary="provider token=summary-secret produced candidate.",
        recommendations=(
            "Rotate api_key=recommendation-secret now.",
        ),
    )
    object.__setattr__(
        report,
        "summary",
        "provider token=summary-secret produced\x7fcandidate.",
    )
    object.__setattr__(
        report,
        "recommendations",
        ("Rotate\x7fapi_key=recommendation-secret now.",),
    )
    unit_of_work = FakeUnitOfWork(
        build_workflow_run(),
        [evidence],
        [invocation],
        report,
    )
    service = RCAExecutionQueryService(lambda: unit_of_work)

    result = await service.get_result(
        GetRCAExecutionResultQuery(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
        )
    )
    payload = result.to_dict()

    assert payload["evidence"][0]["source"] == "loki token=[REDACTED]"
    assert payload["evidence"][0]["summary"] == (
        "authorization=[REDACTED] needs\\u007freview."
    )
    assert payload["invocations"][0]["input_summary"] == (
        "payload password=[REDACTED]"
    )
    assert payload["invocations"][0]["output_summary"] == (
        "tool token=[REDACTED] returned\\u007frows"
    )
    assert payload["report"]["title"] == "Database password=[REDACTED]"
    assert payload["report"]["summary"] == (
        "provider token=[REDACTED] produced\\u007fcandidate."
    )
    assert payload["report"]["recommendations"] == [
        "Rotate\\u007fapi_key=[REDACTED] now."
    ]
    assert "\x7f" not in str(payload)
    assert "source-secret" not in str(payload)
    assert "summary-secret" not in str(payload)
    assert "input-secret" not in str(payload)
    assert "output-secret" not in str(payload)
    assert "report-secret" not in str(payload)
    assert "recommendation-secret" not in str(payload)


async def test_query_hides_cross_tenant_workflow_before_child_reads() -> None:
    """跨租户查询按资源不存在处理，且不能继续探测子记录。"""
    unit_of_work = FakeUnitOfWork(
        build_workflow_run(),
        [build_evidence(1)],
        [build_invocation(1)],
    )
    service = RCAExecutionQueryService(lambda: unit_of_work)

    with pytest.raises(ResourceNotFound, match="Workflow run not found"):
        await service.get_result(
            GetRCAExecutionResultQuery(
                tenant_id="tenant_other",
                workflow_run_id="wfr_001",
            )
        )

    assert unit_of_work.evidence.calls == []
    assert unit_of_work.tool_invocations.calls == []


async def test_query_reports_purged_audit_without_reading_child_tables() -> None:
    """已清理任务应明确返回不可用状态，避免把空列表误解为无异常。"""
    workflow_run = build_workflow_run()
    workflow_run.audit_purged_at = NOW
    unit_of_work = FakeUnitOfWork(
        workflow_run,
        [build_evidence(1)],
        [build_invocation(1)],
        build_report(),
    )
    service = RCAExecutionQueryService(lambda: unit_of_work)

    result = await service.get_result(
        GetRCAExecutionResultQuery(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
        )
    )
    payload = result.to_dict()

    assert payload["audit_available"] is False
    assert payload["audit_purged_at"] == NOW.isoformat()
    assert payload["evidence"] == []
    assert payload["invocations"] == []
    assert payload["report"]["conclusion_status"] == "UNDETERMINED"
    assert unit_of_work.evidence.calls == []
    assert unit_of_work.tool_invocations.calls == []


async def test_query_rechecks_watermark_after_concurrent_purge() -> None:
    """清理在查询中途提交时，不能把空子表误报为可用审计。"""
    workflow_run = build_workflow_run()
    unit_of_work = FakeUnitOfWork(workflow_run, [], [])

    async def list_and_mark_purged(
        tenant_id: str,
        workflow_run_id: str,
        *,
        limit: int,
    ) -> list:
        unit_of_work.evidence.calls.append(
            (tenant_id, workflow_run_id, limit)
        )
        workflow_run.audit_purged_at = NOW
        return []

    unit_of_work.evidence.list_by_workflow_run = list_and_mark_purged
    service = RCAExecutionQueryService(lambda: unit_of_work)

    result = await service.get_result(
        GetRCAExecutionResultQuery(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
        )
    )

    assert result.to_dict()["audit_available"] is False
    assert result.evidence == ()
    assert result.invocations == ()


@pytest.mark.parametrize("limit", [0, 101, True])
def test_query_rejects_invalid_limit(limit: object) -> None:
    """应用查询契约必须独立于 FastAPI 再做一次容量校验。"""
    with pytest.raises(AppValidationError, match="limit"):
        GetRCAExecutionResultQuery(
            tenant_id="tenant_001",
            workflow_run_id="wfr_001",
            limit=limit,  # type: ignore[arg-type]
        )
