import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from devops_agent_platform.agent.llm_report_generator import (
    LLMRCAReportGeneratorConfig,
    ResilientLLMRCAReportGenerator,
)
from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
    RootCauseType,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.ports.llm import LLMReportRequest
from devops_agent_platform.ports.rca_report import (
    LLMReportGenerationOutcome,
)

NOW = datetime(2026, 6, 30, 20, 0, tzinfo=UTC)


def build_command() -> ExecuteRCAWorkflowCommand:
    """构造当前 RCA 执行命令。"""
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
    *,
    summary: str | None = None,
    source: str = "observability",
) -> Evidence:
    """构造包含敏感原始内容、但摘要可安全外发的 Evidence。"""
    content_json, content_sha256 = build_evidence_content(
        {
            "authorization": "Bearer secret-token",
            "result": evidence_type.value,
        }
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
        source=source,
        summary=summary or f"{evidence_type.value} summary",
        content_json=content_json,
        content_sha256=content_sha256,
        confidence=0.9,
        collected_at=NOW,
    )


def valid_response(*evidence_ids: str) -> dict[str, Any]:
    """构造符合严格模型契约的候选根因响应。"""
    return {
        "conclusion_status": "CANDIDATE",
        "title": "Database latency is the leading candidate",
        "summary": "Metrics and logs show correlated database latency.",
        "confidence": 0.82,
        "evidence_ids": list(evidence_ids),
        "recommendations": [
            "Review the database connection pool before remediation."
        ],
    }


def candidate_response(
    candidate: Any,
    *evidence_ids: str,
    root_type: str | None = None,
    conclusion_status: str = "CANDIDATE",
    root_cause: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """鏋勯€犱竴涓湁鍊欓€夊尮閰嶆牎楠岀殑 LLM 鍝嶅簲銆?"""
    selected_root_type = root_type or candidate.root_type.value
    selected_root_cause = root_cause
    if selected_root_cause is None and conclusion_status == "CANDIDATE":
        selected_root_cause = {
            "service": candidate.service,
            "type": selected_root_type,
            "resource": candidate.resource,
        }
    return {
        "selected_candidate_id": candidate.candidate_id,
        "root_cause": selected_root_cause,
        "conclusion_status": conclusion_status,
        "title": "Structured RCA candidate",
        "summary": "The deterministic candidate review is complete.",
        "confidence": 0.82,
        "evidence_ids": list(evidence_ids),
        "recommendations": ["Review the selected candidate before remediation."],
    }


class RecordingGateway:
    """记录请求并按队列返回结果的测试模型网关。"""

    def __init__(self, outcomes: list[Mapping[str, Any] | Exception]) -> None:
        self.outcomes = outcomes
        self.requests: list[LLMReportRequest] = []

    async def generate_report(
        self,
        request: LLMReportRequest,
    ) -> Mapping[str, Any]:
        """返回下一个响应或抛出预设异常。"""
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingObserver:
    """记录固定 LLM 生成结果，不接触业务身份。"""

    def __init__(self) -> None:
        self.observations: list[
            tuple[LLMReportGenerationOutcome, float]
        ] = []

    def observe_llm_report(
        self,
        outcome: LLMReportGenerationOutcome,
        duration_seconds: float,
    ) -> None:
        self.observations.append((outcome, duration_seconds))


async def test_valid_response_builds_stable_structured_report() -> None:
    """有效响应应生成可审计、引用计数一致且可重放的报告。"""
    log = build_evidence("b" * 64, EvidenceType.LOG)
    metric = build_evidence("a" * 64, EvidenceType.METRIC)
    response = valid_response(metric.evidence_id, log.evidence_id)
    gateway = RecordingGateway([response, response])
    observer = RecordingObserver()
    generator = ResilientLLMRCAReportGenerator(
        gateway,
        clock=lambda: NOW,
        observer=observer,
    )

    report = await generator.generate(build_command(), (log, metric))
    replay = await generator.generate(build_command(), (metric, log))

    assert report.conclusion_status is RCAConclusionStatus.CANDIDATE
    assert report.generator_name == "llm-structured-report"
    assert report.generator_version == "v1/rca-report-v2-candidate-review"
    assert report.evidence_type_counts == (("LOG", 1), ("METRIC", 1))
    assert report.report_id == replay.report_id
    assert [item[0] for item in observer.observations] == [
        LLMReportGenerationOutcome.SUCCESS,
        LLMReportGenerationOutcome.SUCCESS,
    ]


async def test_candidate_response_persists_structured_root_cause() -> None:
    log = build_evidence(
        "a" * 64,
        EvidenceType.LOG,
        summary="inventory-service database timeout DB_TIMEOUT",
    )
    trace = build_evidence(
        "b" * 64,
        EvidenceType.TRACE,
        summary="inventory-service database timeout trace",
    )

    class CandidateSelectingGateway:
        def __init__(self) -> None:
            self.requests: list[LLMReportRequest] = []

        async def generate_report(
            self,
            request: LLMReportRequest,
        ) -> Mapping[str, Any]:
            self.requests.append(request)
            candidate = request.candidates[0]
            return {
                "selected_candidate_id": candidate.candidate_id,
                "root_cause": {
                    "service": candidate.service,
                    "type": candidate.root_type.value,
                    "resource": candidate.resource,
                },
                "conclusion_status": "CANDIDATE",
                "title": "Inventory database timeout candidate",
                "summary": "Logs and traces identify the inventory database timeout.",
                "confidence": 0.99,
                "evidence_ids": [log.evidence_id, trace.evidence_id],
                "recommendations": ["Review the database connection health."],
            }

    gateway = CandidateSelectingGateway()
    report = await ResilientLLMRCAReportGenerator(
        gateway,
        clock=lambda: NOW,
    ).generate(build_command(), (log, trace))

    assert gateway.requests[0].candidates
    assert report.suspected_root_node == "inventory-service"
    assert report.root_cause_type is RootCauseType.DEPENDENCY_TIMEOUT
    assert report.root_cause_resource == "postgres"
    assert report.selected_candidate_id == report.root_cause_candidates[0].candidate_id
    assert report.confidence == 0.9


async def test_candidate_type_alias_is_normalized_before_candidate_match() -> None:
    """LLM 鍙娇鐢ㄥ垯绾﹀畾鍒悕锛岄鍩熷眰蹇呴』鏄犲皠鍚庡啀涓€鑷存€ф牎楠屻€?"""
    evidence = build_evidence(
        "a" * 64,
        EvidenceType.LOG,
        summary="inventory-service database timeout DB_TIMEOUT",
    )
    trace = build_evidence(
        "b" * 64,
        EvidenceType.TRACE,
        summary="inventory-service database timeout trace",
    )

    class AliasGateway:
        async def generate_report(
            self,
            request: LLMReportRequest,
        ) -> Mapping[str, Any]:
            candidate = request.candidates[0]
            return candidate_response(
                candidate,
                evidence.evidence_id,
                trace.evidence_id,
                root_type="db_timeout",
            )

    report = await ResilientLLMRCAReportGenerator(
        AliasGateway(),
        clock=lambda: NOW,
    ).generate(build_command(), (evidence, trace))

    assert report.generator_name == "llm-structured-report"
    assert report.root_cause_type is RootCauseType.DEPENDENCY_TIMEOUT
    assert report.root_cause_resource == "postgres"


async def test_no_actionable_root_cause_is_persisted_as_structured_status() -> None:
    """确认无客户影响时，报告保留结论状态但不伪造一个可执行根因。"""
    log = build_evidence(
        "a" * 64,
        EvidenceType.LOG,
        summary=(
            "checkout-service alert remains successful; no customer impact "
            "and no failure counter"
        ),
    )
    metric = build_evidence(
        "b" * 64,
        EvidenceType.METRIC,
        summary=(
            "checkout-service no customer impact; no failure counter; "
            "remains successful"
        ),
    )

    class NoActionGateway:
        async def generate_report(
            self,
            request: LLMReportRequest,
        ) -> Mapping[str, Any]:
            candidate = request.candidates[0]
            assert candidate.root_type is RootCauseType.NO_ACTIONABLE_ROOT_CAUSE
            return candidate_response(
                candidate,
                log.evidence_id,
                metric.evidence_id,
                conclusion_status="NO_ACTIONABLE_ROOT_CAUSE",
            )

    report = await ResilientLLMRCAReportGenerator(
        NoActionGateway(),
        clock=lambda: NOW,
    ).generate(build_command(), (log, metric))

    assert report.generator_name == "llm-structured-report"
    assert report.conclusion_status is RCAConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE
    assert report.root_cause_type is None
    assert report.suspected_root_node is None
    assert report.selected_candidate_id == report.root_cause_candidates[0].candidate_id


@pytest.mark.parametrize(
    ("selected_candidate", "root_type"),
    [
        ("cand-does-not-exist", "dependency_timeout"),
        ("candidate", "dependency_latency"),
    ],
)
async def test_inconsistent_candidate_selection_fails_closed(
    selected_candidate: str,
    root_type: str,
) -> None:
    """未知候选或根因类型不匹配时，不能让模型绕过候选集合写入报告。"""
    evidence = build_evidence(
        "a" * 64,
        EvidenceType.LOG,
        summary="inventory-service database timeout DB_TIMEOUT",
    )

    class InconsistentGateway:
        async def generate_report(
            self,
            request: LLMReportRequest,
        ) -> Mapping[str, Any]:
            candidate = request.candidates[0]
            return {
                **candidate_response(
                    candidate,
                    evidence.evidence_id,
                    root_type=root_type,
                ),
                "selected_candidate_id": (
                    candidate.candidate_id
                    if selected_candidate == "candidate"
                    else selected_candidate
                ),
            }

    report = await ResilientLLMRCAReportGenerator(
        InconsistentGateway(),
        clock=lambda: NOW,
    ).generate(build_command(), (evidence,))

    assert report.generator_name == "deterministic-evidence-summary"
    assert report.conclusion_status is RCAConclusionStatus.UNDETERMINED


async def test_low_score_selected_candidate_falls_back_to_undetermined() -> None:
    """低于安全阈值的候选即使被 LLM 选择，也必须降级为未确定。"""
    evidence = build_evidence(
        "a" * 64,
        EvidenceType.KNOWLEDGE,
        summary="historical only: resembles old incident",
    )

    class LowScoreGateway:
        async def generate_report(
            self,
            request: LLMReportRequest,
        ) -> Mapping[str, Any]:
            candidate = request.candidates[0]
            assert candidate.score < 0.5
            return candidate_response(candidate, evidence.evidence_id)

    report = await ResilientLLMRCAReportGenerator(
        LowScoreGateway(),
        clock=lambda: NOW,
    ).generate(build_command(), (evidence,))

    assert report.generator_name == "deterministic-evidence-summary"
    assert report.conclusion_status is RCAConclusionStatus.UNDETERMINED
    assert report.confidence == 0.0


async def test_request_exposes_only_limited_evidence_projection() -> None:
    """模型请求不能携带原始 Evidence 内容，并且摘要必须限长。"""
    evidence = build_evidence(
        "a" * 64,
        EvidenceType.LOG,
        summary="safe-summary-" * 20,
    )
    gateway = RecordingGateway([valid_response(evidence.evidence_id)])
    generator = ResilientLLMRCAReportGenerator(
        gateway,
        config=LLMRCAReportGeneratorConfig(max_summary_chars=64),
        clock=lambda: NOW,
    )

    await generator.generate(build_command(), (evidence,))

    projection = gateway.requests[0].evidence[0]
    assert len(projection.summary) == 64
    assert not hasattr(projection, "content_json")
    assert "secret-token" not in repr(gateway.requests[0])


async def test_request_redacts_summary_and_source_before_llm_boundary() -> None:
    """Evidence摘要外发给模型前必须二次脱敏，不只依赖工具上游。"""
    evidence = build_evidence(
        "a" * 64,
        EvidenceType.LOG,
        source="loki token=source-secret",
        summary="Authorization: Bearer summary-secret",
    )
    gateway = RecordingGateway([valid_response(evidence.evidence_id)])
    generator = ResilientLLMRCAReportGenerator(
        gateway,
        clock=lambda: NOW,
    )

    await generator.generate(build_command(), (evidence,))

    projection = gateway.requests[0].evidence[0]
    assert projection.source == "loki token=[REDACTED]"
    assert projection.summary == "Authorization: [REDACTED]"
    assert "source-secret" not in repr(gateway.requests[0])
    assert "summary-secret" not in repr(gateway.requests[0])


async def test_response_text_is_redacted_before_report_storage() -> None:
    """模型可能复述敏感信息，落成RCAReport前必须再次脱敏。"""
    evidence = build_evidence("a" * 64, EvidenceType.LOG)
    response = {
        **valid_response(evidence.evidence_id),
        "title": "Database password=hunter2 candidate",
        "summary": "Provider token=secret-token was copied by model.",
        "recommendations": ["Rotate api_key=private-key now."],
    }
    gateway = RecordingGateway([response])
    generator = ResilientLLMRCAReportGenerator(
        gateway,
        clock=lambda: NOW,
    )

    report = await generator.generate(build_command(), (evidence,))

    assert report.generator_name == "llm-structured-report"
    assert report.title == "Database password=[REDACTED] candidate"
    assert report.summary == (
        "Provider token=[REDACTED] was copied by model."
    )
    assert report.recommendations == (
        "Rotate api_key=[REDACTED] now.",
    )
    assert "hunter2" not in repr(report)
    assert "secret-token" not in repr(report)
    assert "private-key" not in repr(report)


async def test_response_with_del_character_uses_deterministic_fallback() -> None:
    """模型正文含 DEL 时应按脏响应降级，不能生成不可审计报告。"""
    evidence = build_evidence("a" * 64, EvidenceType.LOG)
    response = {
        **valid_response(evidence.evidence_id),
        "summary": "Database latency\x7fforged output.",
    }
    observer = RecordingObserver()
    generator = ResilientLLMRCAReportGenerator(
        RecordingGateway([response]),
        clock=lambda: NOW,
        observer=observer,
    )

    report = await generator.generate(build_command(), (evidence,))

    assert report.generator_name == "deterministic-evidence-summary"
    assert "\x7f" not in repr(report)
    assert observer.observations[0][0] is (
        LLMReportGenerationOutcome.FALLBACK_INVALID_RESPONSE
    )


async def test_timeout_falls_back_without_retrying() -> None:
    """模型超时应只调用一次，并立即返回确定性未判定报告。"""

    class SlowGateway:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_report(
            self,
            request: LLMReportRequest,
        ) -> Mapping[str, Any]:
            del request
            self.calls += 1
            await asyncio.sleep(1)
            return {}

    gateway = SlowGateway()
    observer = RecordingObserver()
    evidence = build_evidence("a" * 64, EvidenceType.LOG)
    generator = ResilientLLMRCAReportGenerator(
        gateway,
        config=LLMRCAReportGeneratorConfig(timeout_seconds=0.01),
        clock=lambda: NOW,
        observer=observer,
    )

    report = await generator.generate(build_command(), (evidence,))

    assert report.conclusion_status is RCAConclusionStatus.UNDETERMINED
    assert report.generator_name == "deterministic-evidence-summary"
    assert gateway.calls == 1
    assert observer.observations[0][0] is (
        LLMReportGenerationOutcome.FALLBACK_TIMEOUT
    )


@pytest.mark.parametrize(
    "response",
    [
        {
            **valid_response("a" * 64),
            "unexpected": "must be rejected",
        },
        valid_response("f" * 64),
        {
            **valid_response("a" * 64),
            "confidence": float("nan"),
        },
        {
            **valid_response("a" * 64),
            "conclusion_status": "CONFIRMED",
        },
    ],
)
async def test_untrusted_response_violation_falls_back(
    response: Mapping[str, Any],
) -> None:
    """未知字段、幻觉引用和非有限数字都不能进入领域报告。"""
    evidence = build_evidence("a" * 64, EvidenceType.LOG)
    gateway = RecordingGateway([response])
    generator = ResilientLLMRCAReportGenerator(
        gateway,
        clock=lambda: NOW,
    )

    report = await generator.generate(build_command(), (evidence,))

    assert report.conclusion_status is RCAConclusionStatus.UNDETERMINED
    assert report.generator_name == "deterministic-evidence-summary"


async def test_cancellation_propagates_instead_of_becoming_fallback() -> None:
    """部署关停或上游取消必须向外传播，不能伪装成业务成功。"""
    entered = asyncio.Event()

    class BlockingGateway:
        async def generate_report(
            self,
            request: LLMReportRequest,
        ) -> Mapping[str, Any]:
            del request
            entered.set()
            await asyncio.Event().wait()
            return {}

    evidence = build_evidence("a" * 64, EvidenceType.LOG)
    generator = ResilientLLMRCAReportGenerator(
        BlockingGateway(),
        clock=lambda: NOW,
    )
    task = asyncio.create_task(
        generator.generate(build_command(), (evidence,))
    )
    await entered.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_open_circuit_skips_gateway_until_recovery_probe() -> None:
    """达到失败阈值后跳过网关，恢复窗口后用一次成功探针闭合。"""
    evidence = build_evidence("a" * 64, EvidenceType.LOG)
    now = [100.0]
    gateway = RecordingGateway(
        [
            RuntimeError("provider unavailable"),
            valid_response(evidence.evidence_id),
            valid_response(evidence.evidence_id),
        ]
    )
    observer = RecordingObserver()
    generator = ResilientLLMRCAReportGenerator(
        gateway,
        config=LLMRCAReportGeneratorConfig(
            failure_threshold=1,
            recovery_timeout_seconds=10,
        ),
        clock=lambda: NOW,
        monotonic_clock=lambda: now[0],
        observer=observer,
    )

    failed = await generator.generate(build_command(), (evidence,))
    skipped = await generator.generate(build_command(), (evidence,))
    now[0] = 110.0
    recovered = await generator.generate(build_command(), (evidence,))
    normal = await generator.generate(build_command(), (evidence,))

    assert failed.conclusion_status is RCAConclusionStatus.UNDETERMINED
    assert skipped.conclusion_status is RCAConclusionStatus.UNDETERMINED
    assert recovered.conclusion_status is RCAConclusionStatus.CANDIDATE
    assert normal.conclusion_status is RCAConclusionStatus.CANDIDATE
    assert len(gateway.requests) == 3
    assert [item[0] for item in observer.observations] == [
        LLMReportGenerationOutcome.FALLBACK_PROVIDER_ERROR,
        LLMReportGenerationOutcome.FALLBACK_CIRCUIT_OPEN,
        LLMReportGenerationOutcome.SUCCESS,
        LLMReportGenerationOutcome.SUCCESS,
    ]


async def test_observer_failure_does_not_change_successful_report() -> None:
    """Metrics实现异常不能让已生成的报告失败或降级。"""

    class FailingObserver:
        def observe_llm_report(self, outcome, duration_seconds):
            del outcome, duration_seconds
            raise RuntimeError("metrics registry failed")

    evidence = build_evidence("a" * 64, EvidenceType.LOG)
    generator = ResilientLLMRCAReportGenerator(
        RecordingGateway([valid_response(evidence.evidence_id)]),
        clock=lambda: NOW,
        observer=FailingObserver(),
    )

    report = await generator.generate(build_command(), (evidence,))

    assert report.conclusion_status is RCAConclusionStatus.CANDIDATE


async def test_half_open_allows_only_one_concurrent_probe() -> None:
    """半开阶段只允许一个探针，其他请求直接走确定性降级。"""
    evidence = build_evidence("a" * 64, EvidenceType.LOG)
    now = [100.0]
    entered = asyncio.Event()
    release = asyncio.Event()

    class ProbeGateway:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_report(
            self,
            request: LLMReportRequest,
        ) -> Mapping[str, Any]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider unavailable")
            entered.set()
            await release.wait()
            return valid_response(request.evidence[0].evidence_id)

    gateway = ProbeGateway()
    generator = ResilientLLMRCAReportGenerator(
        gateway,
        config=LLMRCAReportGeneratorConfig(
            failure_threshold=1,
            recovery_timeout_seconds=10,
        ),
        clock=lambda: NOW,
        monotonic_clock=lambda: now[0],
    )
    await generator.generate(build_command(), (evidence,))
    now[0] = 110.0
    probe = asyncio.create_task(
        generator.generate(build_command(), (evidence,))
    )
    await entered.wait()

    concurrent = await generator.generate(build_command(), (evidence,))
    release.set()
    recovered = await probe

    assert concurrent.conclusion_status is RCAConclusionStatus.UNDETERMINED
    assert recovered.conclusion_status is RCAConclusionStatus.CANDIDATE
    assert gateway.calls == 2


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("timeout_seconds", 0),
        ("max_evidence_items", 101),
        ("max_summary_chars", 32),
        ("failure_threshold", True),
        ("recovery_timeout_seconds", float("inf")),
        ("generator_version", "v1\nforged"),
        ("prompt_version", " "),
        ("prompt_version", "rca-report-v1\tforged"),
        ("prompt_version", "rca-report-v1\x7fforged"),
    ],
)
def test_config_rejects_unsafe_values(
    field_name: str,
    value: Any,
) -> None:
    """不安全容量、时间和版本配置必须在启动装配阶段失败。"""
    with pytest.raises(AppValidationError):
        LLMRCAReportGeneratorConfig(**{field_name: value})
