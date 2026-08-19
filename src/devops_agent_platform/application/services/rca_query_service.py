from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from devops_agent_platform.application.queries.rca_results import (
    GetRCAExecutionResultQuery,
)
from devops_agent_platform.domain.exceptions import ResourceNotFound
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.rca_report import RCAReport, RCAReportCandidate
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]


@dataclass(frozen=True)
class RCAEvidenceView:
    """可安全返回给管理接口的 Evidence 摘要视图。"""

    evidence_id: str
    step_id: str
    tool_name: str
    tool_version: str
    evidence_type: str
    source: str
    summary: str
    content_sha256: str
    confidence: float
    collected_at: datetime

    @classmethod
    def from_domain(cls, evidence: Evidence) -> "RCAEvidenceView":
        """从领域对象提取非原文、非敏感字段。"""
        return cls(
            evidence_id=evidence.evidence_id,
            step_id=evidence.step_id,
            tool_name=evidence.tool_name,
            tool_version=evidence.tool_version,
            evidence_type=evidence.evidence_type.value,
            source=_safe_output_text(
                evidence.source,
                maximum=128,
                multiline=False,
            ),
            summary=_safe_output_text(
                evidence.summary,
                maximum=4096,
                multiline=True,
            ),
            content_sha256=evidence.content_sha256,
            confidence=float(evidence.confidence),
            collected_at=evidence.collected_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为接口层可直接序列化的安全字典。"""
        return {
            "evidence_id": self.evidence_id,
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "evidence_type": self.evidence_type,
            "source": self.source,
            "summary": self.summary,
            "content_sha256": self.content_sha256,
            "confidence": self.confidence,
            "collected_at": self.collected_at.isoformat(),
        }


@dataclass(frozen=True)
class RCAToolInvocationView:
    """可安全返回的工具调用审计视图。"""

    invocation_id: str
    step_id: str
    tool_name: str
    tool_version: str
    risk_level: str
    status: str
    input_summary: str
    input_sha256: str
    output_summary: str | None
    output_sha256: str | None
    latency_ms: int
    error_code: str | None
    started_at: datetime
    ended_at: datetime

    @classmethod
    def from_domain(
        cls,
        invocation: ToolInvocation,
    ) -> "RCAToolInvocationView":
        """从领域对象提取不包含原始工具参数的审计字段。"""
        return cls(
            invocation_id=invocation.invocation_id,
            step_id=invocation.step_id,
            tool_name=invocation.tool_name,
            tool_version=invocation.tool_version,
            risk_level=invocation.risk_level.value,
            status=invocation.status.value,
            input_summary=_safe_output_text(
                invocation.input_summary,
                maximum=256,
                multiline=False,
            ),
            input_sha256=invocation.input_sha256,
            output_summary=(
                _safe_output_text(
                    invocation.output_summary,
                    maximum=4096,
                    multiline=True,
                )
                if invocation.output_summary is not None
                else None
            ),
            output_sha256=invocation.output_sha256,
            latency_ms=invocation.latency_ms,
            error_code=invocation.error_code,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定接口字段，不暴露工具原始输入输出。"""
        return {
            "invocation_id": self.invocation_id,
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "risk_level": self.risk_level,
            "status": self.status,
            "input_summary": self.input_summary,
            "input_sha256": self.input_sha256,
            "output_summary": self.output_summary,
            "output_sha256": self.output_sha256,
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
        }


@dataclass(frozen=True)
class RCAReportView:
    """管理端可读取的结构化 RCA 报告视图。"""

    report_id: str
    conclusion_status: str
    title: str
    summary: str
    confidence: float
    evidence_ids: tuple[str, ...]
    evidence_type_counts: tuple[tuple[str, int], ...]
    recommendations: tuple[str, ...]
    generator_name: str
    generator_version: str
    generated_at: datetime
    root_cause: tuple[str, str, str | None] | None
    selected_candidate_id: str | None
    root_cause_candidates: tuple[RCAReportCandidate, ...]

    @classmethod
    def from_domain(cls, report: RCAReport) -> "RCAReportView":
        """从领域报告提取稳定接口字段。"""
        return cls(
            report_id=report.report_id,
            conclusion_status=report.conclusion_status.value,
            title=_safe_output_text(
                report.title,
                maximum=256,
                multiline=False,
            ),
            summary=_safe_output_text(
                report.summary,
                maximum=4096,
                multiline=True,
            ),
            confidence=float(report.confidence),
            evidence_ids=report.evidence_ids,
            evidence_type_counts=report.evidence_type_counts,
            recommendations=tuple(
                _safe_output_text(
                    item,
                    maximum=1024,
                    multiline=False,
                )
                for item in report.recommendations
            ),
            generator_name=report.generator_name,
            generator_version=report.generator_version,
            generated_at=report.generated_at,
            root_cause=(
                (
                    report.suspected_root_node,
                    report.root_cause_type.value,
                    report.root_cause_resource,
                )
                if report.suspected_root_node is not None
                and report.root_cause_type is not None
                else None
            ),
            selected_candidate_id=report.selected_candidate_id,
            root_cause_candidates=report.root_cause_candidates,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化的报告响应。"""
        return {
            "report_id": self.report_id,
            "conclusion_status": self.conclusion_status,
            "title": self.title,
            "summary": self.summary,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "evidence_type_counts": {
                name: count
                for name, count in self.evidence_type_counts
            },
            "recommendations": list(self.recommendations),
            "generator_name": self.generator_name,
            "generator_version": self.generator_version,
            "generated_at": self.generated_at.isoformat(),
            "root_cause": (
                {
                    "service": self.root_cause[0],
                    "type": self.root_cause[1],
                    "resource": self.root_cause[2],
                }
                if self.root_cause is not None
                else None
            ),
            "selected_candidate_id": self.selected_candidate_id,
            "root_cause_candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "root_cause": {
                        "service": item.service,
                        "type": item.root_type.value,
                        "resource": item.resource,
                    },
                    "score": item.score,
                    "supporting_evidence_ids": list(
                        item.supporting_evidence_ids
                    ),
                    "contradicting_evidence_ids": list(
                        item.contradicting_evidence_ids
                    ),
                    "source_evidence_types": list(item.source_evidence_types),
                    "missing_evidence": list(item.missing_evidence),
                }
                for item in self.root_cause_candidates
            ],
        }


@dataclass(frozen=True)
class RCAExecutionResultView:
    """管理端读取的一次 RCA 工作流安全结果快照。"""

    workflow_run_id: str
    incident_id: str
    operator_id: str
    status: str
    version: int
    execution_attempts: int
    step_count: int
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    trace_id: str
    audit_purged_at: datetime | None
    canceled_by: str | None
    cancellation_reason: str | None
    canceled_at: datetime | None
    report: RCAReportView | None
    evidence: tuple[RCAEvidenceView, ...]
    invocations: tuple[RCAToolInvocationView, ...]
    evidence_truncated: bool
    invocations_truncated: bool

    @classmethod
    def build(
        cls,
        workflow_run: WorkflowRun,
        evidence: list[Evidence],
        invocations: list[ToolInvocation],
        report: RCAReport | None,
        *,
        limit: int,
    ) -> "RCAExecutionResultView":
        """截断子记录并构造不包含租约和幂等哈希的查询结果。"""
        return cls(
            workflow_run_id=workflow_run.workflow_run_id,
            incident_id=workflow_run.incident_id,
            operator_id=workflow_run.operator_id,
            status=workflow_run.status.value,
            version=workflow_run.version,
            execution_attempts=workflow_run.execution_attempts,
            step_count=workflow_run.step_count,
            created_at=workflow_run.created_at,
            started_at=workflow_run.started_at,
            ended_at=workflow_run.ended_at,
            trace_id=workflow_run.trace_id,
            audit_purged_at=workflow_run.audit_purged_at,
            canceled_by=workflow_run.canceled_by,
            cancellation_reason=(
                _safe_output_text(
                    workflow_run.cancellation_reason,
                    maximum=2048,
                    multiline=True,
                )
                if workflow_run.cancellation_reason is not None
                else None
            ),
            canceled_at=workflow_run.canceled_at,
            report=(
                RCAReportView.from_domain(report)
                if report is not None
                else None
            ),
            evidence=tuple(
                RCAEvidenceView.from_domain(item)
                for item in evidence[:limit]
            ),
            invocations=tuple(
                RCAToolInvocationView.from_domain(item)
                for item in invocations[:limit]
            ),
            evidence_truncated=len(evidence) > limit,
            invocations_truncated=len(invocations) > limit,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为统一响应 envelope 中的 data。"""
        return {
            "workflow_run_id": self.workflow_run_id,
            "incident_id": self.incident_id,
            "operator_id": self.operator_id,
            "status": self.status,
            "version": self.version,
            "execution_attempts": self.execution_attempts,
            "step_count": self.step_count,
            "created_at": self.created_at.isoformat(),
            "started_at": _optional_isoformat(self.started_at),
            "ended_at": _optional_isoformat(self.ended_at),
            "trace_id": self.trace_id,
            "audit_available": self.audit_purged_at is None,
            "audit_purged_at": _optional_isoformat(
                self.audit_purged_at
            ),
            "canceled_by": self.canceled_by,
            "cancellation_reason": self.cancellation_reason,
            "canceled_at": _optional_isoformat(self.canceled_at),
            "report": (
                self.report.to_dict()
                if self.report is not None
                else None
            ),
            "evidence": [item.to_dict() for item in self.evidence],
            "invocations": [item.to_dict() for item in self.invocations],
            "evidence_truncated": self.evidence_truncated,
            "invocations_truncated": self.invocations_truncated,
        }


class RCAExecutionQueryService:
    """读取单次 RCA 工作流及其有限审计结果。"""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def get_result(
        self,
        query: GetRCAExecutionResultQuery,
    ) -> RCAExecutionResultView:
        """先验证租户归属，再读取有限 Evidence 和调用记录。"""
        async with self._unit_of_work_factory() as unit_of_work:
            workflow_run = await unit_of_work.workflow_runs.get_by_id(
                query.tenant_id,
                query.workflow_run_id,
            )
            if workflow_run is None:
                raise ResourceNotFound("Workflow run not found")
            report = await unit_of_work.rca_reports.get_by_workflow_run(
                query.tenant_id,
                query.workflow_run_id,
            )
            if workflow_run.audit_purged_at is not None:
                return RCAExecutionResultView.build(
                    workflow_run,
                    [],
                    [],
                    report,
                    limit=query.limit,
                )

            fetch_limit = query.limit + 1
            evidence = await unit_of_work.evidence.list_by_workflow_run(
                query.tenant_id,
                query.workflow_run_id,
                limit=fetch_limit,
            )
            invocations = (
                await unit_of_work.tool_invocations.list_by_workflow_run(
                    query.tenant_id,
                    query.workflow_run_id,
                    limit=fetch_limit,
                )
            )
            refreshed_workflow = await unit_of_work.workflow_runs.get_by_id(
                query.tenant_id,
                query.workflow_run_id,
            )
            if refreshed_workflow is None:
                raise ResourceNotFound("Workflow run not found")
            if refreshed_workflow.audit_purged_at is not None:
                return RCAExecutionResultView.build(
                    refreshed_workflow,
                    [],
                    [],
                    report,
                    limit=query.limit,
                )
            return RCAExecutionResultView.build(
                refreshed_workflow,
                evidence,
                invocations,
                report,
                limit=query.limit,
            )


def _optional_isoformat(value: datetime | None) -> str | None:
    """把可空时间统一转换为 ISO 8601 字符串。"""
    return value.isoformat() if value is not None else None


def _safe_output_text(
    value: str,
    *,
    maximum: int,
    multiline: bool,
) -> str:
    """查询输出边界兜底转义控制字符并遮蔽敏感片段。"""
    redacted = redact_sensitive_text(value.strip())[0]
    result: list[str] = []
    for character in redacted:
        if character == "\n" and multiline:
            result.append(character)
        elif ord(character) < 32 or ord(character) == 127:
            result.append(f"\\u{ord(character):04x}")
        else:
            result.append(character)
    safe_value = "".join(result)[:maximum].strip()
    return safe_value or "[REDACTED]"
