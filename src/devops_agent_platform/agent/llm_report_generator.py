import asyncio
import hashlib
import json
import logging
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from devops_agent_platform.agent.report_generator import (
    DeterministicRCAReportGenerator,
    validate_report_evidence,
)
from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.domain.enums import RCAConclusionStatus, RootCauseType
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.rca_report import RCAReport, RCAReportCandidate
from devops_agent_platform.ports.llm import (
    LLMReportEvidence,
    LLMReportGatewayPort,
    LLMReportRequest,
)
from devops_agent_platform.ports.rca_report import (
    LLMReportGenerationObserverPort,
    LLMReportGenerationOutcome,
    RCAReportGeneratorPort,
)
from devops_agent_platform.rca_reasoning import (
    ReasoningEvidence,
    ReasoningEvidenceType,
    RootCauseReasoningPipeline,
    RootCauseReasoningResult,
)
from devops_agent_platform.rca_reasoning.taxonomy import RootCauseTaxonomyMapper
from devops_agent_platform.tools.sanitization import redact_sensitive_text

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]

_LEGACY_RESPONSE_FIELDS = frozenset(
    {
        "conclusion_status",
        "title",
        "summary",
        "confidence",
        "evidence_ids",
        "recommendations",
    }
)
_CANDIDATE_RESPONSE_FIELDS = _LEGACY_RESPONSE_FIELDS | {
    "selected_candidate_id",
    "root_cause",
}
_SERVICE_PATTERN = re.compile(
    r"\b([a-z][a-z0-9-]+(?:-service|-api|-worker|-gateway))\b",
    re.IGNORECASE,
)
_ROOT_CAUSE_TAXONOMY = RootCauseTaxonomyMapper()


@dataclass(frozen=True)
class LLMRCAReportGeneratorConfig:
    """LLM 报告生成器的容量、超时和进程内熔断配置。"""

    timeout_seconds: float = 30.0
    max_evidence_items: int = 50
    max_summary_chars: int = 2000
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 60.0
    generator_version: str = "v1"
    prompt_version: str = "rca-report-v2-candidate-review"

    def __post_init__(self) -> None:
        """拒绝可能导致无界等待、超大 Prompt 或熔断失效的配置。"""
        _validate_positive_number(
            "timeout_seconds",
            self.timeout_seconds,
            maximum=120.0,
        )
        _validate_bounded_integer(
            "max_evidence_items",
            self.max_evidence_items,
            minimum=1,
            maximum=100,
        )
        _validate_bounded_integer(
            "max_summary_chars",
            self.max_summary_chars,
            minimum=64,
            maximum=4096,
        )
        _validate_bounded_integer(
            "failure_threshold",
            self.failure_threshold,
            minimum=1,
            maximum=100,
        )
        _validate_positive_number(
            "recovery_timeout_seconds",
            self.recovery_timeout_seconds,
            maximum=3600.0,
        )
        _validate_text("generator_version", self.generator_version, 24)
        _validate_text("prompt_version", self.prompt_version, 32)


@dataclass(frozen=True)
class _CircuitPermit:
    """标识一次模型调用属于哪个熔断代次。"""

    generation: int
    half_open: bool


@dataclass(frozen=True)
class _CandidateSelection:
    selected_candidate_id: str | None
    root_service: str | None
    root_type: RootCauseType | None
    root_resource: str | None
    candidate: RCAReportCandidate | None


class ResilientLLMRCAReportGenerator:
    """带严格响应校验、超时、熔断和确定性降级的 LLM 报告生成器。

    熔断器只保护当前进程。多 Worker 或多副本部署若需要全局熔断，应在模型
    网关、服务网格或共享状态层实现，不能把这里的内存状态误认为集群级状态。
    """

    GENERATOR_NAME = "llm-structured-report"

    def __init__(
        self,
        gateway: LLMReportGatewayPort,
        *,
        config: LLMRCAReportGeneratorConfig | None = None,
        fallback: RCAReportGeneratorPort | None = None,
        clock: Clock | None = None,
        monotonic_clock: MonotonicClock | None = None,
        observer: LLMReportGenerationObserverPort | None = None,
    ) -> None:
        """装配模型网关、确定性降级器以及可测试时钟。"""
        self._gateway = gateway
        self._config = config or LLMRCAReportGeneratorConfig()
        self._fallback = fallback or DeterministicRCAReportGenerator(
            clock=clock
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._observer = observer
        self._circuit_lock = asyncio.Lock()
        self._circuit_generation = 0
        self._consecutive_failures = 0
        self._open_until: float | None = None
        self._half_open_in_progress = False

    async def generate(
        self,
        command: ExecuteRCAWorkflowCommand,
        evidence: tuple[Evidence, ...],
    ) -> RCAReport:
        """调用模型生成报告；外部故障或脏响应一律确定性降级。"""
        validate_report_evidence(command, evidence)
        started_tick = self._read_monotonic()
        selected = tuple(
            sorted(evidence, key=lambda item: item.evidence_id)[
                : self._config.max_evidence_items
            ]
        )
        reasoning = self._build_reasoning(selected)
        request = self._build_request(command, selected, reasoning)
        permit = await self._acquire_circuit_permit()
        if permit is None:
            self._observe(
                LLMReportGenerationOutcome.FALLBACK_CIRCUIT_OPEN,
                started_tick,
            )
            return await self._fallback.generate(command, evidence)

        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                response = await self._gateway.generate_report(request)
            report = self._build_report(command, selected, response, reasoning)
        except asyncio.CancelledError:
            await self._release_cancelled_permit(permit)
            self._observe(
                LLMReportGenerationOutcome.CANCELLED,
                started_tick,
            )
            raise
        except TimeoutError:
            await self._record_failure(permit)
            self._log_fallback(
                LLMReportGenerationOutcome.FALLBACK_TIMEOUT,
                command,
                "timeout",
            )
            self._observe(
                LLMReportGenerationOutcome.FALLBACK_TIMEOUT,
                started_tick,
            )
            return await self._fallback.generate(command, evidence)
        except AppValidationError as exc:
            await self._record_failure(permit)
            self._log_fallback(
                LLMReportGenerationOutcome.FALLBACK_INVALID_RESPONSE,
                command,
                str(exc),
            )
            self._observe(
                LLMReportGenerationOutcome.FALLBACK_INVALID_RESPONSE,
                started_tick,
            )
            return await self._fallback.generate(command, evidence)
        except Exception as exc:
            await self._record_failure(permit)
            self._log_fallback(
                LLMReportGenerationOutcome.FALLBACK_PROVIDER_ERROR,
                command,
                type(exc).__name__,
            )
            self._observe(
                LLMReportGenerationOutcome.FALLBACK_PROVIDER_ERROR,
                started_tick,
            )
            return await self._fallback.generate(command, evidence)

        await self._record_success(permit)
        self._observe(LLMReportGenerationOutcome.SUCCESS, started_tick)
        return report

    def _build_request(
        self,
        command: ExecuteRCAWorkflowCommand,
        evidence: tuple[Evidence, ...],
        reasoning: RootCauseReasoningResult,
    ) -> LLMReportRequest:
        """只投影已脱敏的有限摘要，原始 Evidence 内容永远不进入模型请求。"""
        projections = tuple(
            LLMReportEvidence(
                evidence_id=item.evidence_id,
                evidence_type=item.evidence_type,
                source=redact_sensitive_text(item.source)[0],
                summary=redact_sensitive_text(
                    item.summary[: self._config.max_summary_chars]
                )[0],
                confidence=float(item.confidence),
            )
            for item in evidence
        )
        return LLMReportRequest(
            tenant_id=command.tenant_id,
            incident_id=command.incident_id,
            workflow_run_id=command.workflow_run_id,
            execution_attempt=command.execution_attempt,
            trace_id=command.trace_id,
            prompt_version=self._config.prompt_version,
            evidence=projections,
            candidates=_to_report_candidates(reasoning),
            recommended_status=RCAConclusionStatus(
                reasoning.recommended_status.value
            ),
        )

    def _build_report(
        self,
        command: ExecuteRCAWorkflowCommand,
        evidence: tuple[Evidence, ...],
        response: Mapping[str, Any],
        reasoning: RootCauseReasoningResult,
    ) -> RCAReport:
        """把不可信模型响应转换为受领域约束保护的不可变报告。"""
        if not isinstance(response, Mapping):
            raise AppValidationError("LLM report response must be a mapping")
        response_fields = set(response)
        if response_fields not in {
            _LEGACY_RESPONSE_FIELDS,
            _CANDIDATE_RESPONSE_FIELDS,
        }:
            raise AppValidationError(
                "LLM report response fields do not match the contract"
            )

        conclusion_status = self._parse_status(response["conclusion_status"])
        selection = self._parse_candidate_selection(
            response,
            conclusion_status=conclusion_status,
            reasoning=reasoning,
            candidate_contract=response_fields == _CANDIDATE_RESPONSE_FIELDS,
        )
        title = _require_redacted_text("title", response["title"], 256)
        summary = _require_redacted_text(
            "summary",
            response["summary"],
            4096,
        )
        confidence = _require_confidence(response["confidence"])
        if selection.candidate is not None:
            confidence = _calibrate_confidence(confidence, selection.candidate)
        evidence_ids = _require_string_list(
            "evidence_ids",
            response["evidence_ids"],
            maximum_items=len(evidence),
            item_maximum=64,
            allow_empty=False,
        )
        recommendations = _require_redacted_string_list(
            "recommendations",
            response["recommendations"],
            maximum_items=20,
            item_maximum=1024,
            allow_empty=True,
        )

        evidence_by_id = {item.evidence_id: item for item in evidence}
        if not set(evidence_ids).issubset(evidence_by_id):
            raise AppValidationError(
                "LLM report references evidence outside the model request"
            )
        cited_evidence = tuple(
            evidence_by_id[evidence_id] for evidence_id in evidence_ids
        )
        type_counts = Counter(
            item.evidence_type.value for item in cited_evidence
        )
        normalized = {
            "conclusion_status": conclusion_status.value,
            "title": title,
            "summary": summary,
            "confidence": confidence,
            "evidence_ids": evidence_ids,
            "recommendations": recommendations,
            "selected_candidate_id": selection.selected_candidate_id,
            "root_cause": (
                {
                    "service": selection.root_service,
                    "type": (
                        selection.root_type.value
                        if selection.root_type is not None
                        else None
                    ),
                    "resource": selection.root_resource,
                }
                if selection.root_type is not None
                else None
            ),
        }
        report_candidates = _to_report_candidates(reasoning)
        return RCAReport(
            report_id=self._build_report_id(
                command,
                cited_evidence,
                normalized,
            ),
            tenant_id=command.tenant_id,
            incident_id=command.incident_id,
            workflow_run_id=command.workflow_run_id,
            execution_attempt=command.execution_attempt,
            conclusion_status=conclusion_status,
            title=title,
            summary=summary,
            confidence=confidence,
            evidence_ids=evidence_ids,
            evidence_type_counts=tuple(sorted(type_counts.items())),
            recommendations=recommendations,
            generator_name=self.GENERATOR_NAME,
            generator_version=(
                f"{self._config.generator_version}/"
                f"{self._config.prompt_version}"
            ),
            generated_at=self._now(),
            suspected_root_node=selection.root_service,
            root_cause_type=selection.root_type,
            root_cause_resource=selection.root_resource,
            selected_candidate_id=selection.selected_candidate_id,
            root_cause_candidates=report_candidates,
        )

    @staticmethod
    def _build_reasoning(
        evidence: tuple[Evidence, ...],
    ) -> RootCauseReasoningResult:
        summaries = " ".join(item.summary for item in evidence)
        services = _SERVICE_PATTERN.findall(summaries)
        incident_service = services[-1].casefold() if services else "unknown-service"
        return RootCauseReasoningPipeline().reason(
            incident_service=incident_service,
            incident_summary=summaries[:4096],
            evidence=tuple(
                ReasoningEvidence(
                    evidence_id=item.evidence_id,
                    evidence_type=ReasoningEvidenceType.from_value(
                        item.evidence_type.value
                    ),
                    source=item.source,
                    summary=item.summary,
                    confidence=float(item.confidence),
                )
                for item in evidence
            ),
        )

    @staticmethod
    def _parse_status(value: Any) -> RCAConclusionStatus:
        """只接受领域枚举中明确声明的结论状态。"""
        if not isinstance(value, str):
            raise AppValidationError(
                "conclusion_status must be a string"
            )
        try:
            status = RCAConclusionStatus(value)
        except ValueError as exc:
            raise AppValidationError(
                "conclusion_status is not supported"
            ) from exc
        if status is RCAConclusionStatus.CONFIRMED:
            raise AppValidationError(
                "LLM reports cannot confirm a root cause"
            )
        return status

    @staticmethod
    def _parse_candidate_selection(
        response: Mapping[str, Any],
        *,
        conclusion_status: RCAConclusionStatus,
        reasoning: RootCauseReasoningResult,
        candidate_contract: bool,
    ) -> _CandidateSelection:
        if not candidate_contract:
            if conclusion_status is RCAConclusionStatus.UNDETERMINED:
                return _CandidateSelection(None, None, None, None, None)
            candidates = _to_report_candidates(reasoning)
            selected = candidates[0] if candidates else None
            if selected is None:
                raise AppValidationError(
                    "legacy candidate response has no deterministic candidate"
                )
            if conclusion_status is RCAConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE:
                if selected.root_type is not RootCauseType.NO_ACTIONABLE_ROOT_CAUSE:
                    raise AppValidationError(
                        "legacy no-action conclusion is inconsistent"
                    )
                return _CandidateSelection(
                    selected.candidate_id,
                    None,
                    None,
                    None,
                    selected,
                )
            if conclusion_status is not RCAConclusionStatus.CANDIDATE:
                raise AppValidationError("legacy candidate conclusion is invalid")
            return _CandidateSelection(
                selected.candidate_id,
                selected.service,
                selected.root_type,
                selected.resource,
                selected,
            )
        selected_value = response["selected_candidate_id"]
        root_value = response["root_cause"]
        if conclusion_status is RCAConclusionStatus.UNDETERMINED:
            if selected_value is not None or root_value is not None:
                raise AppValidationError(
                    "undetermined report cannot select a root cause candidate"
                )
            return _CandidateSelection(None, None, None, None, None)
        candidates = _to_report_candidates(reasoning)
        try:
            selected_id = _require_text(
                "selected_candidate_id",
                selected_value,
                64,
            )
        except AppValidationError:
            if len(candidates) != 1:
                raise
            selected_id = candidates[0].candidate_id
        selected = next(
            (item for item in candidates if item.candidate_id == selected_id),
            None,
        )
        if selected is None and len(candidates) == 1:
            selected = candidates[0]
            selected_id = selected.candidate_id
        if selected is None:
            raise AppValidationError("selected candidate is not in candidate set")
        if conclusion_status is RCAConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE:
            if (
                root_value is not None
                or selected.root_type is not RootCauseType.NO_ACTIONABLE_ROOT_CAUSE
            ):
                raise AppValidationError("no-actionable conclusion is inconsistent")
            return _CandidateSelection(selected_id, None, None, None, selected)
        if conclusion_status is not RCAConclusionStatus.CANDIDATE:
            raise AppValidationError("candidate conclusion status is invalid")
        if not isinstance(root_value, Mapping) or set(root_value) != {
            "service",
            "type",
            "resource",
        }:
            raise AppValidationError("root_cause must match the candidate schema")
        service = _require_text("root_cause.service", root_value["service"], 128)
        raw_type = _require_text("root_cause.type", root_value["type"], 128)
        # Models may use a documented alias (for example ``db_timeout``), but
        # the canonical type must still match the deterministic candidate.
        root_type = _ROOT_CAUSE_TAXONOMY.map_type(raw_type)
        if (
            root_type is RootCauseType.UNKNOWN
            and raw_type != RootCauseType.UNKNOWN.value
        ):
            raise AppValidationError("root_cause.type is not supported")
        resource_value = root_value["resource"]
        resource = (
            _require_text("root_cause.resource", resource_value, 256)
            if resource_value is not None
            else None
        )
        if (
            service != selected.service
            or root_type is not selected.root_type
            or resource != selected.resource
        ):
            raise AppValidationError("root cause does not match selected candidate")
        return _CandidateSelection(
            selected_id,
            service,
            root_type,
            resource,
            selected,
        )

    def _build_report_id(
        self,
        command: ExecuteRCAWorkflowCommand,
        evidence: tuple[Evidence, ...],
        normalized_response: Mapping[str, Any],
    ) -> str:
        """根据执行身份、版本、模型输出和证据内容生成稳定报告 ID。"""
        response_json = json.dumps(
            normalized_response,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        parts = [
            command.tenant_id,
            command.workflow_run_id,
            str(command.execution_attempt),
            self.GENERATOR_NAME,
            self._config.generator_version,
            self._config.prompt_version,
            response_json,
        ]
        parts.extend(
            f"{item.evidence_id}:{item.content_sha256}"
            for item in evidence
        )
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    async def _acquire_circuit_permit(self) -> _CircuitPermit | None:
        """在锁内判断闭合、打开和半开状态，保证只有一个半开探针。"""
        now = self._read_monotonic()
        async with self._circuit_lock:
            if self._open_until is not None:
                if now < self._open_until:
                    return None
                if self._half_open_in_progress:
                    return None
                self._half_open_in_progress = True
                return _CircuitPermit(
                    generation=self._circuit_generation,
                    half_open=True,
                )
            return _CircuitPermit(
                generation=self._circuit_generation,
                half_open=False,
            )

    async def _record_success(self, permit: _CircuitPermit) -> None:
        """仅允许当前熔断代次的成功结果关闭或重置熔断器。"""
        async with self._circuit_lock:
            if permit.generation != self._circuit_generation:
                return
            self._consecutive_failures = 0
            if permit.half_open:
                self._circuit_generation += 1
                self._open_until = None
                self._half_open_in_progress = False

    async def _record_failure(self, permit: _CircuitPermit) -> None:
        """累计连续失败，达到阈值或半开失败时重新打开熔断器。"""
        now = self._read_monotonic()
        async with self._circuit_lock:
            if permit.generation != self._circuit_generation:
                return
            self._consecutive_failures += 1
            if (
                permit.half_open
                or self._consecutive_failures
                >= self._config.failure_threshold
            ):
                self._circuit_generation += 1
                self._consecutive_failures = 0
                self._open_until = (
                    now + self._config.recovery_timeout_seconds
                )
                self._half_open_in_progress = False

    async def _release_cancelled_permit(
        self,
        permit: _CircuitPermit,
    ) -> None:
        """取消不计为供应商失败，但必须释放半开探针名额。"""
        async with self._circuit_lock:
            if (
                permit.generation == self._circuit_generation
                and permit.half_open
            ):
                self._half_open_in_progress = False

    def _now(self) -> datetime:
        """读取带时区时钟并统一为 UTC。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)

    def _read_monotonic(self) -> float:
        """读取有限的单调时钟值，避免测试或错误注入破坏熔断状态。"""
        value = self._monotonic_clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise AppValidationError(
                "monotonic clock must return a finite number"
            )
        return float(value)

    def _observe(
        self,
        outcome: LLMReportGenerationOutcome,
        started_tick: float,
    ) -> None:
        """尽力上报固定结果；监控故障不能改变报告生成结果。"""
        if self._observer is None:
            return
        try:
            duration = max(0.0, self._read_monotonic() - started_tick)
            self._observer.observe_llm_report(outcome, duration)
        except Exception:
            # Metrics 属于非关键路径，不能因采集器故障触发业务降级或失败。
            return

    @staticmethod
    def _log_fallback(
        outcome: LLMReportGenerationOutcome,
        command: ExecuteRCAWorkflowCommand,
        reason: str,
    ) -> None:
        """Log a bounded fallback reason without provider bodies or credentials."""

        logger.warning(
            "LLM RCA report generation fell back",
            extra={
                "event": "llm_rca_report_fallback",
                "outcome": outcome.value,
                "tenant_id": command.tenant_id,
                "incident_id": command.incident_id,
                "workflow_run_id": command.workflow_run_id,
                "execution_attempt": command.execution_attempt,
                "reason": reason[:256],
            },
        )


def _to_report_candidates(
    reasoning: RootCauseReasoningResult,
) -> tuple[RCAReportCandidate, ...]:
    return tuple(
        RCAReportCandidate(
            candidate_id=item.candidate_id,
            service=item.identity.service,
            root_type=item.identity.root_type,
            resource=item.identity.resource,
            score=item.final_score,
            supporting_evidence_ids=item.supporting_evidence_ids,
            contradicting_evidence_ids=item.contradicting_evidence_ids,
            source_evidence_types=tuple(
                value.value for value in item.source_evidence_types
            ),
            missing_evidence=item.missing_evidence,
        )
        for item in reasoning.candidates
    )


def _calibrate_confidence(
    model_confidence: float,
    candidate: RCAReportCandidate,
) -> float:
    if candidate.score < 0.5:
        raise AppValidationError("selected candidate score is below the safe threshold")
    if candidate.score >= 0.8 and not (
        candidate.contradicting_evidence_ids or candidate.missing_evidence
    ):
        cap = 0.9
    else:
        cap = 0.7
    return min(model_confidence, cap)


def _validate_bounded_integer(
    field_name: str,
    value: int,
    *,
    minimum: int,
    maximum: int,
) -> None:
    """校验配置整数上下界，并拒绝 bool 冒充整数。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise AppValidationError(
            f"{field_name} must be between {minimum} and {maximum}"
        )


def _validate_positive_number(
    field_name: str,
    value: float,
    *,
    maximum: float,
) -> None:
    """校验有限正数配置，避免无限等待和异常熔断窗口。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or not 0 < float(value) <= maximum
    ):
        raise AppValidationError(
            f"{field_name} must be greater than 0 and at most {maximum}"
        )


def _validate_text(field_name: str, value: str, maximum: int) -> None:
    """校验配置文本，并拒绝所有 ASCII 控制字符。"""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise AppValidationError(
            f"{field_name} must be a trimmed non-empty string"
        )


def _require_text(field_name: str, value: Any, maximum: int) -> str:
    """读取有限模型文本，并把不可见字符视为响应契约违规。"""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise AppValidationError(f"{field_name} is invalid")
    return value


def _require_redacted_text(
    field_name: str,
    value: Any,
    maximum: int,
) -> str:
    """读取模型文本并在进入领域对象前遮蔽敏感片段。"""
    text = _require_text(field_name, value, maximum)
    redacted = redact_sensitive_text(text)[0]
    if len(redacted) > maximum:
        redacted = redacted[:maximum].rstrip()
    if not redacted:
        raise AppValidationError(f"{field_name} is invalid after redaction")
    return redacted


def _require_confidence(value: Any) -> float:
    """读取有限的模型置信度，拒绝 bool、NaN 和 Infinity。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 1
    ):
        raise AppValidationError("confidence must be between 0 and 1")
    return float(value)


def _require_string_list(
    field_name: str,
    value: Any,
    *,
    maximum_items: int,
    item_maximum: int,
    allow_empty: bool,
) -> tuple[str, ...]:
    """把 JSON 字符串数组转换成唯一元组，并限制数量与单项长度。"""
    minimum_items = 0 if allow_empty else 1
    if (
        not isinstance(value, list)
        or not minimum_items <= len(value) <= maximum_items
    ):
        raise AppValidationError(f"{field_name} has invalid item count")
    result = tuple(
        _require_text(field_name, item, item_maximum)
        for item in value
    )
    if len(result) != len(set(result)):
        raise AppValidationError(f"{field_name} items must be unique")
    return result


def _require_redacted_string_list(
    field_name: str,
    value: Any,
    *,
    maximum_items: int,
    item_maximum: int,
    allow_empty: bool,
) -> tuple[str, ...]:
    """读取模型字符串数组，并保证脱敏后仍满足唯一性约束。"""
    result = tuple(
        _require_redacted_text(field_name, item, item_maximum)
        for item in _require_string_list(
            field_name,
            value,
            maximum_items=maximum_items,
            item_maximum=item_maximum,
            allow_empty=allow_empty,
        )
    )
    if len(result) != len(set(result)):
        raise AppValidationError(
            f"{field_name} items must be unique after redaction"
        )
    return result
