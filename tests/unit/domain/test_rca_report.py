from dataclasses import replace
from datetime import UTC, datetime

import pytest

from devops_agent_platform.agent.report_generator import (
    DeterministicRCAReportGenerator,
)
from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.domain.models.rca_report import RCAReport

NOW = datetime(2026, 6, 30, 18, 0, tzinfo=UTC)


def build_command() -> ExecuteRCAWorkflowCommand:
    """构造报告生成使用的当前执行命令。"""
    return ExecuteRCAWorkflowCommand(
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        operator_id="operator_001",
        worker_id="worker_001",
        execution_attempt=1,
        trace_id="trc_001",
    )


def build_evidence(
    evidence_id: str,
    evidence_type: EvidenceType,
) -> Evidence:
    """构造报告引用的有效 Evidence。"""
    content_json, content_sha256 = build_evidence_content(
        {"status": "ok", "source": evidence_type.value}
    )
    return Evidence(
        evidence_id=evidence_id,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        step_id=f"collect.{evidence_type.value.lower()}",
        tool_name="observability.query",
        tool_version="v1",
        evidence_type=evidence_type,
        source="observability",
        summary=f"{evidence_type.value} evidence",
        content_json=content_json,
        content_sha256=content_sha256,
        confidence=1.0,
        collected_at=NOW,
    )


def build_report() -> RCAReport:
    """构造满足完整领域约束的基础报告。"""
    return RCAReport(
        report_id="a" * 64,
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        conclusion_status=RCAConclusionStatus.UNDETERMINED,
        title="Root cause requires review",
        summary="Evidence was collected.",
        confidence=0.0,
        evidence_ids=("b" * 64,),
        evidence_type_counts=(("LOG", 1),),
        recommendations=("Review cited evidence.",),
        generator_name="deterministic",
        generator_version="v1",
        generated_at=NOW,
    )


async def test_deterministic_generator_never_claims_unverified_root_cause() -> None:
    """基础生成器只汇总证据，并明确输出未确定根因。"""
    evidence = (
        build_evidence("b" * 64, EvidenceType.LOG),
        build_evidence("a" * 64, EvidenceType.METRIC),
        build_evidence("c" * 64, EvidenceType.LOG),
    )
    generator = DeterministicRCAReportGenerator(clock=lambda: NOW)

    report = await generator.generate(build_command(), evidence)
    replay = await generator.generate(build_command(), tuple(reversed(evidence)))

    assert report.conclusion_status is RCAConclusionStatus.UNDETERMINED
    assert report.confidence == 0.0
    assert report.evidence_ids == ("a" * 64, "b" * 64, "c" * 64)
    assert report.evidence_type_counts == (("LOG", 2), ("METRIC", 1))
    assert report.report_id == replay.report_id
    assert "No verified root cause" in report.summary


async def test_generator_rejects_cross_execution_evidence() -> None:
    """跨租户或旧执行代次 Evidence 不能进入当前报告。"""
    evidence = replace(
        build_evidence("a" * 64, EvidenceType.LOG),
        execution_attempt=2,
    )

    with pytest.raises(AppValidationError, match="does not match"):
        await DeterministicRCAReportGenerator(clock=lambda: NOW).generate(
            build_command(),
            (evidence,),
        )


def test_report_rejects_inconsistent_type_counts() -> None:
    """报告计数总和必须与 Evidence 引用数量一致。"""
    with pytest.raises(AppValidationError, match="total"):
        replace(
            build_report(),
            evidence_type_counts=(("LOG", 2),),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "Root cause\x7frequires review"},
        {"summary": "Evidence\twas collected."},
        {"summary": "Evidence\x7fwas collected."},
        {"recommendations": ("Review\x7fcited evidence.",)},
        {"generator_version": "v1\x7fforged"},
    ],
)
def test_report_rejects_control_contaminated_text(
    changes: dict[str, object],
) -> None:
    """新报告的展示文本与审计身份都不能包含不可见字符。"""
    with pytest.raises(AppValidationError, match="control characters"):
        replace(build_report(), **changes)


def test_report_allows_newlines_only_in_summary() -> None:
    """摘要允许结构化换行，但标题和单条建议仍保持单行。"""
    report = replace(
        build_report(),
        summary="Evidence was collected.\nReview remains required.",
    )

    assert "\n" in report.summary
    with pytest.raises(AppValidationError, match="control characters"):
        replace(report, title="Root cause\nrequires review")
    with pytest.raises(AppValidationError, match="control characters"):
        replace(report, recommendations=("Review\ncited evidence.",))


async def test_generator_rejects_naive_clock() -> None:
    """报告时间必须可跨时区比较。"""
    generator = DeterministicRCAReportGenerator(
        clock=lambda: NOW.replace(tzinfo=None)
    )

    with pytest.raises(AppValidationError, match="timezone-aware"):
        await generator.generate(
            build_command(),
            (build_evidence("a" * 64, EvidenceType.LOG),),
        )
