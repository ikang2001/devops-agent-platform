import asyncio
import hashlib
import json
import math
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
from devops_agent_platform.domain.enums import RCAConclusionStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.rca_report import RCAReport
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
from devops_agent_platform.tools.sanitization import redact_sensitive_text

Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]

_RESPONSE_FIELDS = frozenset(
    {
        "conclusion_status",
        "title",
        "summary",
        "confidence",
        "evidence_ids",
        "recommendations",
    }
)


@dataclass(frozen=True)
class LLMRCAReportGeneratorConfig:
    """LLM 报告生成器的容量、超时和进程内熔断配置。"""

    timeout_seconds: float = 30.0
    max_evidence_items: int = 50
    max_summary_chars: int = 2000
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 60.0
    generator_version: str = "v1"
    prompt_version: str = "rca-report-v1"

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
        request = self._build_request(command, selected)
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
            report = self._build_report(command, selected, response)
        except asyncio.CancelledError:
            await self._release_cancelled_permit(permit)
            self._observe(
                LLMReportGenerationOutcome.CANCELLED,
                started_tick,
            )
            raise
        except TimeoutError:
            await self._record_failure(permit)
            self._observe(
                LLMReportGenerationOutcome.FALLBACK_TIMEOUT,
                started_tick,
            )
            return await self._fallback.generate(command, evidence)
        except AppValidationError:
            await self._record_failure(permit)
            self._observe(
                LLMReportGenerationOutcome.FALLBACK_INVALID_RESPONSE,
                started_tick,
            )
            return await self._fallback.generate(command, evidence)
        except Exception:
            await self._record_failure(permit)
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
        )

    def _build_report(
        self,
        command: ExecuteRCAWorkflowCommand,
        evidence: tuple[Evidence, ...],
        response: Mapping[str, Any],
    ) -> RCAReport:
        """把不可信模型响应转换为受领域约束保护的不可变报告。"""
        if not isinstance(response, Mapping):
            raise AppValidationError("LLM report response must be a mapping")
        if set(response) != _RESPONSE_FIELDS:
            raise AppValidationError(
                "LLM report response fields do not match the contract"
            )

        conclusion_status = self._parse_status(response["conclusion_status"])
        title = _require_redacted_text("title", response["title"], 256)
        summary = _require_redacted_text(
            "summary",
            response["summary"],
            4096,
        )
        confidence = _require_confidence(response["confidence"])
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
        }
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
