from __future__ import annotations

import math
from collections.abc import Iterable

from devops_agent_platform.domain.enums import (
    EvidenceType as DomainEvidenceType,
)
from devops_agent_platform.domain.enums import (
    ToolInvocationStatus,
)
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.evaluation.schemas import (
    CausalEdge,
    Claim,
    ClaimType,
    ConclusionStatus,
    EvidenceType,
    RCAPrediction,
    RootCauseCandidateRef,
    RootCauseRef,
    ToolCall,
    ToolStatus,
)

_EVIDENCE_TYPE_MAP = {
    DomainEvidenceType.METRIC: EvidenceType.METRIC,
    DomainEvidenceType.LOG: EvidenceType.LOG,
    DomainEvidenceType.TRACE: EvidenceType.TRACE,
    DomainEvidenceType.DEPLOYMENT: EvidenceType.CHANGE,
    DomainEvidenceType.CHANGE: EvidenceType.CHANGE,
    DomainEvidenceType.RUNBOOK: EvidenceType.RUNBOOK,
    DomainEvidenceType.INCIDENT_HISTORY: EvidenceType.KNOWLEDGE,
    DomainEvidenceType.TOPOLOGY: EvidenceType.TOPOLOGY,
    DomainEvidenceType.KNOWLEDGE: EvidenceType.KNOWLEDGE,
}


class RCAReportPredictionAdapter:
    """把已持久化 RCA 快照转换为 Benchmark 结构化 Prediction。

    适配器只转换运行时事实：不会读取 Scenario Manifest、Ground Truth 或原始
    工具输出。报告没有结构化根因类型时，候选/确认结论会保守降级，避免从
    ``summary`` 等自然语言字段猜测根因。
    """

    def adapt(
        self,
        report: RCAReport,
        evidence: Iterable[Evidence],
        invocations: Iterable[ToolInvocation],
        *,
        scenario_id: str,
        root_cause_type: str | None = None,
        root_cause_service: str | None = None,
        root_cause_resource: str | None = None,
        latency_ms: int | None = None,
        llm_calls: int = 0,
        total_tokens: int = 0,
        estimated_cost: float = 0.0,
    ) -> RCAPrediction:
        """转换一次工作流结果，并在跨租户/执行代次时 fail-closed。"""
        report.validate()
        self.validate_measurement_inputs(
            llm_calls=llm_calls,
            total_tokens=total_tokens,
            estimated_cost=estimated_cost,
        )
        evidence_items = tuple(evidence)
        invocation_items = tuple(invocations)
        self._validate_runtime_records(report, evidence_items, invocation_items)
        evidence_types = self._map_evidence_types(evidence_items)
        tool_calls = self._map_tool_calls(evidence_items, invocation_items)
        root_cause, conclusion_status, confidence, claims = self._map_conclusion(
            report,
            root_cause_type=root_cause_type,
            root_cause_service=root_cause_service,
            root_cause_resource=root_cause_resource,
        )
        measured_latency = self._resolve_latency(invocation_items, latency_ms)
        return RCAPrediction(
            scenario_id=scenario_id,
            run_id=report.workflow_run_id,
            root_cause=root_cause,
            conclusion_status=conclusion_status,
            confidence=confidence,
            evidence_ids=report.evidence_ids,
            evidence_types=evidence_types,
            claims=claims,
            causal_chain=tuple(
                CausalEdge(
                    from_node=source,
                    to_node=target,
                    evidence_ids=evidence_ids,
                )
                for source, target, evidence_ids in report.causal_chain
            ),
            affected_services=report.affected_services,
            root_cause_candidates=self._map_candidates(report),
            tool_calls=tool_calls,
            investigation_steps=len(invocation_items),
            llm_calls=llm_calls,
            latency_ms=measured_latency,
            total_tokens=total_tokens,
            estimated_cost=estimated_cost,
        )

    @staticmethod
    def _validate_runtime_records(
        report: RCAReport,
        evidence: tuple[Evidence, ...],
        invocations: tuple[ToolInvocation, ...],
    ) -> None:
        expected_ids = set(report.evidence_ids)
        actual_ids = {item.evidence_id for item in evidence}
        if actual_ids != expected_ids or len(evidence) != len(actual_ids):
            raise ValueError(
                "runtime evidence must exactly match the report evidence references"
            )
        for item in evidence:
            if (
                item.tenant_id != report.tenant_id
                or item.incident_id != report.incident_id
                or item.workflow_run_id != report.workflow_run_id
                or item.execution_attempt != report.execution_attempt
            ):
                raise ValueError("runtime evidence does not match report execution")
        for item in invocations:
            if (
                item.tenant_id != report.tenant_id
                or item.incident_id != report.incident_id
                or item.workflow_run_id != report.workflow_run_id
                or item.execution_attempt != report.execution_attempt
            ):
                raise ValueError("runtime invocation does not match report execution")

    @staticmethod
    def _map_evidence_types(evidence: tuple[Evidence, ...]) -> tuple[EvidenceType, ...]:
        mapped = {_EVIDENCE_TYPE_MAP[item.evidence_type] for item in evidence}
        return tuple(sorted(mapped, key=lambda item: item.value))

    @staticmethod
    def _map_tool_calls(
        evidence: tuple[Evidence, ...],
        invocations: tuple[ToolInvocation, ...],
    ) -> tuple[ToolCall, ...]:
        evidence_by_step: dict[tuple[str, str], list[str]] = {}
        for item in evidence:
            evidence_by_step.setdefault((item.step_id, item.tool_name), []).append(
                item.evidence_id
            )
        return tuple(
            ToolCall(
                tool_type=RCAReportPredictionAdapter._tool_type(item),
                status=(
                    ToolStatus.SUCCEEDED
                    if item.status is ToolInvocationStatus.SUCCEEDED
                    else ToolStatus.FAILED
                ),
                evidence_ids=tuple(
                    evidence_by_step.get((item.step_id, item.tool_name), ())
                ),
            )
            for item in invocations
        )

    @staticmethod
    def _tool_type(item: ToolInvocation) -> str:
        if "@" in item.tool_name:
            return item.tool_name
        return f"{item.tool_name}@{item.tool_version}"

    @staticmethod
    def _map_conclusion(
        report: RCAReport,
        *,
        root_cause_type: str | None,
        root_cause_service: str | None,
        root_cause_resource: str | None,
    ) -> tuple[RootCauseRef | None, ConclusionStatus, float, tuple[Claim, ...]]:
        status = ConclusionStatus(report.conclusion_status.value)
        resolved_type = root_cause_type or (
            report.root_cause_type.value
            if report.root_cause_type is not None
            else None
        )
        if (
            report.suspected_root_node is None
            or resolved_type is None
            or status not in {ConclusionStatus.CANDIDATE, ConclusionStatus.CONFIRMED}
        ):
            if status in {ConclusionStatus.CANDIDATE, ConclusionStatus.CONFIRMED}:
                return None, ConclusionStatus.UNDETERMINED, 0.0, ()
            return None, status, report.confidence, ()
        service = root_cause_service or report.suspected_root_node
        root = RootCauseRef(
            service=service,
            type=resolved_type,
            resource=(
                root_cause_resource
                if root_cause_resource is not None
                else report.root_cause_resource
            ),
        )
        statement = report.summary[:2048]
        return (
            root,
            status,
            report.confidence,
            (
                Claim(
                    claim_type=ClaimType.ROOT_CAUSE,
                    statement=statement,
                    evidence_ids=report.evidence_ids,
                ),
            ),
        )

    @staticmethod
    def _map_candidates(report: RCAReport) -> tuple[RootCauseCandidateRef, ...]:
        return tuple(
            RootCauseCandidateRef(
                candidate_id=item.candidate_id,
                root_cause=RootCauseRef(
                    service=item.service,
                    type=item.root_type.value,
                    resource=item.resource,
                ),
                score=item.score,
                supporting_evidence_ids=item.supporting_evidence_ids,
                contradicting_evidence_ids=item.contradicting_evidence_ids,
                source_evidence_types=tuple(
                    EvidenceType(value) for value in item.source_evidence_types
                ),
            )
            for item in report.root_cause_candidates
        )

    @staticmethod
    def _resolve_latency(
        invocations: tuple[ToolInvocation, ...],
        latency_ms: int | None,
    ) -> int:
        if latency_ms is None:
            return sum(item.latency_ms for item in invocations)
        if isinstance(latency_ms, bool) or not isinstance(latency_ms, int):
            raise ValueError("latency_ms must be a non-negative integer")
        if latency_ms < 0:
            raise ValueError("latency_ms must be a non-negative integer")
        return latency_ms

    @staticmethod
    def validate_measurement_inputs(
        *,
        llm_calls: int,
        total_tokens: int,
        estimated_cost: float,
    ) -> None:
        """校验外部计量，拒绝 NaN/负数，避免适配器制造无效成本数据。"""
        if (
            isinstance(llm_calls, bool)
            or not isinstance(llm_calls, int)
            or llm_calls < 0
        ):
            raise ValueError("llm_calls must be a non-negative integer")
        if (
            isinstance(total_tokens, bool)
            or not isinstance(total_tokens, int)
            or total_tokens < 0
        ):
            raise ValueError("total_tokens must be a non-negative integer")
        if (
            isinstance(estimated_cost, bool)
            or not isinstance(estimated_cost, int | float)
            or not math.isfinite(float(estimated_cost))
            or estimated_cost < 0
        ):
            raise ValueError("estimated_cost must be a finite non-negative number")
