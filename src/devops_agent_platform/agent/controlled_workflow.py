import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Protocol

from devops_agent_platform.agent.report_generator import (
    DeterministicRCAReportGenerator,
)
from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.application.exceptions import (
    AgentWorkflowError,
    ToolExecutionTimeoutError,
)
from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
    ToolInvocationStatus,
    ToolRiskLevel,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    PermissionDenied,
)
from devops_agent_platform.domain.models.evidence import (
    Evidence,
    build_evidence_content,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.tool_invocation import (
    ToolInvocation,
    build_tool_payload_sha256,
)
from devops_agent_platform.ports.rca_report import RCAReportGeneratorPort
from devops_agent_platform.ports.workflow import (
    AgentWorkflowExecutionFailure,
    AgentWorkflowResult,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.sanitization import (
    EvidenceSanitizerConfig,
    redact_sensitive_text,
    sanitize_evidence_payload,
)

Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_RESERVED_PAYLOAD_FIELDS = frozenset(
    {
        "execution_attempt",
        "incident_id",
        "operator_id",
        "plan_id",
        "plan_version",
        "step_id",
        "tenant_id",
        "trace_id",
        "worker_id",
        "workflow_run_id",
    }
)
_TOOL_EVIDENCE_DEFAULTS = {
    "metrics.query": (EvidenceType.METRIC, "prometheus"),
    "changes.query": (EvidenceType.CHANGE, "change_event_store"),
    "logs.query": (EvidenceType.LOG, "loki"),
    "traces.query": (EvidenceType.TRACE, "tempo"),
}


class ToolRegistryPort(Protocol):
    """受控工作流读取工具元数据所需的最小注册表能力。"""

    def get(self, tool_name: str, version: str) -> ToolDefinition:
        """按明确名称和版本返回工具定义。"""
        ...


class ToolPermissionPort(Protocol):
    """执行前工具授权检查端口。"""

    async def check(
        self,
        definition: ToolDefinition,
        tenant_id: str,
        operator_id: str | None,
    ) -> None:
        """无权限时抛出PermissionDenied。"""
        ...


class ToolExecutionPort(Protocol):
    """经过授权后执行单个工具的端口。"""

    async def execute(
        self,
        definition: ToolDefinition,
        payload: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        """执行工具并返回可审计的JSON对象。"""
        ...


@dataclass(frozen=True)
class RCAWorkflowStep:
    """静态RCA计划中的一个固定工具步骤。"""

    step_id: str
    tool_name: str
    tool_version: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        """校验步骤身份和不可覆盖的静态JSON参数。"""
        self._validate_identifier("step_id", self.step_id, 128)
        self._validate_identifier("tool_name", self.tool_name, 128)
        if (
            not isinstance(self.tool_version, str)
            or not 1 <= len(self.tool_version) <= 64
            or self.tool_version != self.tool_version.strip()
        ):
            raise AppValidationError("tool_version is invalid")
        if not isinstance(self.payload, Mapping):
            raise AppValidationError("step payload must be a mapping")
        payload_copy = dict(self.payload)
        reserved = sorted(_RESERVED_PAYLOAD_FIELDS.intersection(payload_copy))
        if reserved:
            raise AppValidationError(
                f"step payload contains reserved fields: {', '.join(reserved)}"
            )
        self._validate_json("step payload", payload_copy, 64 * 1024)
        object.__setattr__(self, "payload", MappingProxyType(payload_copy))

    @staticmethod
    def _validate_identifier(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or _IDENTIFIER_PATTERN.fullmatch(value) is None
        ):
            raise AppValidationError(f"{field_name} is invalid")

    @staticmethod
    def _validate_json(
        field_name: str,
        value: object,
        maximum_bytes: int,
    ) -> None:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError) as exc:
            raise AppValidationError(f"{field_name} must be JSON serializable") from exc
        if len(encoded) > maximum_bytes:
            raise AppValidationError(f"{field_name} exceeds {maximum_bytes} bytes")


@dataclass(frozen=True)
class RCAWorkflowPlan:
    """由服务端发布的不可变固定RCA工具计划。"""

    plan_id: str
    version: str
    steps: tuple[RCAWorkflowStep, ...]

    def __post_init__(self) -> None:
        """校验计划标识、版本、非空步骤和步骤ID唯一性。"""
        RCAWorkflowStep._validate_identifier("plan_id", self.plan_id, 128)
        if (
            not isinstance(self.version, str)
            or not 1 <= len(self.version) <= 64
            or self.version != self.version.strip()
        ):
            raise AppValidationError("plan version is invalid")
        if not isinstance(self.steps, tuple) or not self.steps:
            raise AppValidationError("plan steps must be a non-empty tuple")
        if not all(isinstance(step, RCAWorkflowStep) for step in self.steps):
            raise AppValidationError("plan steps must contain only RCAWorkflowStep")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise AppValidationError("plan step_id values must be unique")


@dataclass(frozen=True)
class ControlledAgentWorkflowConfig:
    """受控工作流的步骤、输入输出和风险边界。"""

    max_steps: int = 20
    max_payload_bytes: int = 64 * 1024
    max_result_bytes: int = 64 * 1024
    allowed_risk_levels: frozenset[ToolRiskLevel] = frozenset(
        {ToolRiskLevel.LOW, ToolRiskLevel.MEDIUM}
    )
    evidence_sanitizer: EvidenceSanitizerConfig = field(
        default_factory=EvidenceSanitizerConfig
    )
    # C0：单步工具失败时是否继续后续只读步骤；默认 False 保持历史 fail-fast。
    continue_on_step_failure: bool = False
    # 报告生成器只能在证据覆盖范围内给出候选置信度，不能绕过工作流护栏。
    report_confidence_cap: float = 1.0
    partial_report_confidence_cap: float = 0.4

    def __post_init__(self) -> None:
        """拒绝无界计划、无效容量和未经审批的高风险配置。"""
        self._validate_positive_int("max_steps", self.max_steps, 100)
        self._validate_positive_int(
            "max_payload_bytes",
            self.max_payload_bytes,
            1024 * 1024,
        )
        self._validate_positive_int(
            "max_result_bytes",
            self.max_result_bytes,
            1024 * 1024,
        )
        if (
            not isinstance(self.allowed_risk_levels, frozenset)
            or not self.allowed_risk_levels
            or not all(
                isinstance(level, ToolRiskLevel) for level in self.allowed_risk_levels
            )
        ):
            raise AppValidationError("allowed_risk_levels is invalid")
        if ToolRiskLevel.HIGH in self.allowed_risk_levels:
            raise AppValidationError("HIGH risk tools require an approval port")
        if not isinstance(
            self.evidence_sanitizer,
            EvidenceSanitizerConfig,
        ):
            raise AppValidationError(
                "evidence_sanitizer must be an EvidenceSanitizerConfig"
            )
        if not isinstance(self.continue_on_step_failure, bool):
            raise AppValidationError("continue_on_step_failure must be a boolean")
        self._validate_confidence_cap(
            "report_confidence_cap",
            self.report_confidence_cap,
        )
        self._validate_confidence_cap(
            "partial_report_confidence_cap",
            self.partial_report_confidence_cap,
        )
        if self.partial_report_confidence_cap > self.report_confidence_cap:
            raise AppValidationError(
                "partial_report_confidence_cap must not exceed report_confidence_cap"
            )

    @staticmethod
    def _validate_positive_int(
        field_name: str,
        value: int,
        maximum: int,
    ) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise AppValidationError(f"{field_name} must be between 1 and {maximum}")

    @staticmethod
    def _validate_confidence_cap(field_name: str, value: float) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise AppValidationError(f"{field_name} must be between 0 and 1")


@dataclass(frozen=True)
class _PreparedStep:
    """完成元数据、风险、权限和Payload预检的内部步骤。"""

    step: RCAWorkflowStep
    definition: ToolDefinition
    payload: dict[str, Any]


class ControlledAgentWorkflow:
    """按服务端固定计划顺序执行已授权工具的AgentWorkflowPort适配器。"""

    def __init__(
        self,
        plan: RCAWorkflowPlan,
        registry: ToolRegistryPort,
        permission_checker: ToolPermissionPort,
        tool_executor: ToolExecutionPort,
        config: ControlledAgentWorkflowConfig | None = None,
        clock: Clock | None = None,
        monotonic_clock: MonotonicClock | None = None,
        report_generator: RCAReportGeneratorPort | None = None,
    ) -> None:
        self._plan = plan
        self._registry = registry
        self._permission_checker = permission_checker
        self._tool_executor = tool_executor
        self._config = config or ControlledAgentWorkflowConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._report_generator = report_generator or DeterministicRCAReportGenerator()
        if len(plan.steps) > self._config.max_steps:
            raise AppValidationError(
                f"plan must not exceed {self._config.max_steps} steps"
            )

    async def execute(
        self,
        command: ExecuteRCAWorkflowCommand,
    ) -> AgentWorkflowResult:
        """全量预检后，按固定顺序执行工具步骤并收集结构化证据。

        默认 fail-fast：任一步失败立即抛出 ``AgentWorkflowExecutionFailure``。
        开启 ``continue_on_step_failure`` 后：记录失败调用并继续后续只读步骤；
        若最终至少有一条证据则降级生成报告，否则仍按失败收口。
        """
        prepared_steps = await self._prepare_steps(command)
        evidence_items: list[Evidence] = []
        invocation_items: list[ToolInvocation] = []
        # 记录最后一次步骤失败，供“零证据”场景作为 cause 抛出。
        last_step_failure: Exception | None = None
        failed_step_ids: list[str] = []
        for prepared in prepared_steps:
            started_at = self._now()
            started_tick = self._read_monotonic()
            result: object | None = None
            try:
                async with asyncio.timeout(prepared.definition.timeout_ms / 1000):
                    result = await self._tool_executor.execute(
                        prepared.definition,
                        prepared.payload,
                        command.trace_id,
                    )
                self._validate_tool_result(prepared.step, result)
                sanitized_result = self._sanitize_evidence_result(
                    prepared.step,
                    result,
                )
                evidence = self._build_evidence(
                    command,
                    prepared,
                    sanitized_result,
                )
            except asyncio.CancelledError:
                # 租约失权或进程关闭由协调器处理，取消信号不能包装成普通失败。
                raise
            except Exception as exc:
                failure = exc
                if isinstance(exc, TimeoutError):
                    failure = ToolExecutionTimeoutError(
                        "Tool timed out: "
                        f"{prepared.definition.tool_name}@"
                        f"{prepared.definition.version}"
                    )
                    failure.__cause__ = exc
                invocation_items.append(
                    self._build_invocation(
                        command,
                        prepared,
                        status=ToolInvocationStatus.FAILED,
                        input_payload=prepared.payload,
                        output=None,
                        output_summary=None,
                        error=failure,
                        started_at=started_at,
                        started_tick=started_tick,
                    )
                )
                last_step_failure = failure
                failed_step_ids.append(prepared.step.step_id)
                # 兼容旧行为：未开启降级时立即失败。
                if not self._config.continue_on_step_failure:
                    raise AgentWorkflowExecutionFailure(
                        AgentWorkflowResult(
                            evidence=tuple(evidence_items),
                            invocations=tuple(invocation_items),
                        ),
                        failure,
                    ) from failure
                continue

            evidence_items.append(evidence)
            invocation_items.append(
                self._build_invocation(
                    command,
                    prepared,
                    status=ToolInvocationStatus.SUCCEEDED,
                    input_payload=prepared.payload,
                    output=sanitized_result,
                    output_summary=evidence.summary,
                    error=None,
                    started_at=started_at,
                    started_tick=started_tick,
                )
            )

        workflow_result = AgentWorkflowResult(
            evidence=tuple(evidence_items),
            invocations=tuple(invocation_items),
        )
        # 全部步骤失败或没有任何可引用证据：不能伪装成成功 RCA。
        if not evidence_items:
            failure = last_step_failure or AgentWorkflowError(
                "RCA workflow produced no evidence"
            )
            raise AgentWorkflowExecutionFailure(
                workflow_result,
                failure,
            ) from failure

        try:
            report = await self._report_generator.generate(
                command,
                workflow_result.evidence,
            )
            report = self._apply_report_guardrails(
                report,
                workflow_result.evidence,
                failed_step_ids,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AgentWorkflowExecutionFailure(
                workflow_result,
                exc,
            ) from exc
        return AgentWorkflowResult(
            evidence=workflow_result.evidence,
            invocations=workflow_result.invocations,
            report=report,
        )

    def _apply_report_guardrails(
        self,
        report: RCAReport,
        evidence: tuple[Evidence, ...],
        failed_step_ids: list[str],
    ) -> RCAReport:
        """按计划证据类型覆盖率下调置信度，并标明部分采集。"""
        expected_types = {
            self._evidence_type_for_tool(step.tool_name) for step in self._plan.steps
        }
        collected_types = {item.evidence_type for item in evidence}
        type_coverage = len(expected_types & collected_types) / len(expected_types)
        confidence_cap = min(
            type_coverage,
            float(self._config.report_confidence_cap),
        )
        if failed_step_ids:
            confidence_cap = min(
                confidence_cap,
                float(self._config.partial_report_confidence_cap),
            )
        conclusion_status = report.conclusion_status
        if conclusion_status is RCAConclusionStatus.CONFIRMED:
            conclusion_status = RCAConclusionStatus.UNDETERMINED
        summary = self._append_partial_marker(report.summary, failed_step_ids)
        return replace(
            report,
            conclusion_status=conclusion_status,
            summary=summary,
            confidence=min(float(report.confidence), confidence_cap),
        )

    @staticmethod
    def _append_partial_marker(summary: str, failed_step_ids: list[str]) -> str:
        """保留失败步骤标记；摘要过长时优先截断原始生成内容。"""
        if not failed_step_ids or "Partial collection:" in summary:
            return summary
        marker = " Partial collection: failed steps=" + ",".join(failed_step_ids) + "."
        maximum_summary_length = 4096 - len(marker)
        return summary[:maximum_summary_length].rstrip() + marker

    @staticmethod
    def _evidence_type_for_tool(tool_name: str) -> EvidenceType:
        return _TOOL_EVIDENCE_DEFAULTS.get(
            tool_name,
            (EvidenceType.RUNBOOK, tool_name),
        )[0]

    async def _prepare_steps(
        self,
        command: ExecuteRCAWorkflowCommand,
    ) -> tuple[_PreparedStep, ...]:
        """在任何工具执行前完成全部元数据、风险、授权和输入校验。"""
        prepared_steps: list[_PreparedStep] = []
        for step in self._plan.steps:
            definition = self._registry.get(
                step.tool_name,
                step.tool_version,
            )
            if definition.risk_level not in self._config.allowed_risk_levels:
                raise PermissionDenied(
                    "Tool risk level is not allowed in unattended RCA: "
                    f"{definition.tool_name}@{definition.version}"
                )
            await self._permission_checker.check(
                definition,
                command.tenant_id,
                command.operator_id,
            )
            payload = self._build_payload(step, command)
            RCAWorkflowStep._validate_json(
                "tool payload",
                payload,
                self._config.max_payload_bytes,
            )
            prepared_steps.append(
                _PreparedStep(
                    step=step,
                    definition=definition,
                    payload=payload,
                )
            )
        return tuple(prepared_steps)

    def _build_payload(
        self,
        step: RCAWorkflowStep,
        command: ExecuteRCAWorkflowCommand,
    ) -> dict[str, Any]:
        """把静态参数与不可覆盖的执行上下文合并。"""
        return {
            **dict(step.payload),
            "plan_id": self._plan.plan_id,
            "plan_version": self._plan.version,
            "step_id": step.step_id,
            "tenant_id": command.tenant_id,
            "workflow_run_id": command.workflow_run_id,
            "incident_id": command.incident_id,
            "operator_id": command.operator_id,
            "worker_id": command.worker_id,
            "execution_attempt": command.execution_attempt,
            "trace_id": command.trace_id,
        }

    def _validate_tool_result(
        self,
        step: RCAWorkflowStep,
        result: object,
    ) -> None:
        """限制工具输出类型和体积，防止结果占满Agent进程内存。"""
        if not isinstance(result, dict):
            raise AgentWorkflowError(f"Tool result must be an object: {step.step_id}")
        try:
            encoded = json.dumps(
                result,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError) as exc:
            raise AgentWorkflowError(
                f"Tool result must be JSON serializable: {step.step_id}"
            ) from exc
        if len(encoded) > self._config.max_result_bytes:
            raise AgentWorkflowError(
                "Tool result exceeds "
                f"{self._config.max_result_bytes} bytes: {step.step_id}"
            )

    def _build_evidence(
        self,
        command: ExecuteRCAWorkflowCommand,
        prepared: _PreparedStep,
        result: dict[str, Any],
    ) -> Evidence:
        """把单步工具输出转换为可持久化 Evidence。"""
        evidence_type = self._evidence_type_for_tool(prepared.definition.tool_name)
        default_source = _TOOL_EVIDENCE_DEFAULTS.get(
            prepared.definition.tool_name,
            (evidence_type, prepared.definition.tool_name),
        )[1]
        source = self._extract_source(result, default_source)
        content_json, content_sha256 = build_evidence_content(result)
        return Evidence(
            evidence_id=self._build_evidence_id(
                command,
                prepared.step,
                evidence_type,
                source,
                content_sha256,
            ),
            tenant_id=command.tenant_id,
            incident_id=command.incident_id,
            workflow_run_id=command.workflow_run_id,
            execution_attempt=command.execution_attempt,
            step_id=prepared.step.step_id,
            tool_name=prepared.definition.tool_name,
            tool_version=prepared.definition.version,
            evidence_type=evidence_type,
            source=source,
            summary=self._build_evidence_summary(
                prepared.definition.tool_name,
                source,
                result,
            ),
            content_json=content_json,
            content_sha256=content_sha256,
            confidence=1.0,
            collected_at=self._now(),
        )

    def _sanitize_evidence_result(
        self,
        step: RCAWorkflowStep,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """生成独立脱敏副本，阻止敏感工具输出进入持久化边界。"""
        try:
            sanitized = sanitize_evidence_payload(
                result,
                self._config.evidence_sanitizer,
            )
        except AppValidationError as exc:
            raise AgentWorkflowError(
                f"Tool result violates evidence sanitization policy: {step.step_id}"
            ) from exc
        return sanitized.payload

    def _build_invocation(
        self,
        command: ExecuteRCAWorkflowCommand,
        prepared: _PreparedStep,
        *,
        status: ToolInvocationStatus,
        input_payload: Mapping[str, Any],
        output: Mapping[str, Any] | None,
        output_summary: str | None,
        error: Exception | None,
        started_at: datetime,
        started_tick: float,
    ) -> ToolInvocation:
        """把单次工具执行转换为不含原始入参的审计记录。"""
        ended_at = self._now()
        ended_tick = self._read_monotonic()
        if ended_tick < started_tick:
            raise AppValidationError("monotonic clock moved backwards")
        return ToolInvocation(
            invocation_id=self._build_invocation_id(command, prepared),
            tenant_id=command.tenant_id,
            incident_id=command.incident_id,
            workflow_run_id=command.workflow_run_id,
            execution_attempt=command.execution_attempt,
            step_id=prepared.step.step_id,
            operator_id=command.operator_id,
            trace_id=command.trace_id,
            tool_name=prepared.definition.tool_name,
            tool_version=prepared.definition.version,
            risk_level=prepared.definition.risk_level,
            status=status,
            input_summary=f"payload_fields={len(input_payload)}",
            input_sha256=build_tool_payload_sha256(input_payload),
            output_summary=output_summary,
            output_sha256=(
                build_tool_payload_sha256(output) if output is not None else None
            ),
            latency_ms=round((ended_tick - started_tick) * 1000),
            error_code=type(error).__name__ if error is not None else None,
            started_at=started_at,
            ended_at=ended_at,
        )

    @staticmethod
    def _build_invocation_id(
        command: ExecuteRCAWorkflowCommand,
        prepared: _PreparedStep,
    ) -> str:
        """按执行代次和步骤生成稳定调用 ID，支持数据库幂等约束。"""
        seed = "|".join(
            (
                command.tenant_id,
                command.workflow_run_id,
                str(command.execution_attempt),
                prepared.step.step_id,
                prepared.definition.tool_name,
                prepared.definition.version,
            )
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_evidence_id(
        command: ExecuteRCAWorkflowCommand,
        step: RCAWorkflowStep,
        evidence_type: EvidenceType,
        source: str,
        content_sha256: str,
    ) -> str:
        """按执行身份和内容哈希生成稳定 Evidence ID。"""
        seed = "|".join(
            (
                command.tenant_id,
                command.workflow_run_id,
                str(command.execution_attempt),
                step.step_id,
                evidence_type.value,
                source,
                content_sha256,
            )
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    @staticmethod
    def _extract_source(
        result: Mapping[str, Any],
        default_source: str,
    ) -> str:
        """优先使用工具结果中的短来源字段，并在Evidence边界兜底脱敏。"""
        raw_source = result.get("source")
        if isinstance(raw_source, str):
            source = redact_sensitive_text(raw_source)[0]
            if _is_public_evidence_text(source, 128):
                return source
        return default_source

    @staticmethod
    def _build_evidence_summary(
        tool_name: str,
        source: str,
        result: Mapping[str, Any],
    ) -> str:
        """生成不包含原始大内容、并已脱敏的证据摘要。"""
        raw_summary = result.get("summary")
        if isinstance(raw_summary, str) and _is_public_evidence_text(raw_summary, 4096):
            summary = redact_sensitive_text(raw_summary)[0]
            if _is_public_evidence_text(summary, 4096):
                return summary
        signal_count = 0
        signals = result.get("signals")
        if isinstance(signals, list):
            signal_count = len(signals)
        truncated = bool(result.get("possibly_truncated", False))
        return (
            f"{tool_name} collected evidence from {source}; "
            f"signals={signal_count}; truncated={truncated}"
        )

    def _now(self) -> datetime:
        """读取带时区时钟，避免 Evidence 采集时间受本地时区影响。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)

    def _read_monotonic(self) -> float:
        """读取有限单调时钟值，用于不受系统校时影响的延迟计算。"""
        value = self._monotonic_clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise AppValidationError("monotonic_clock must return a finite number")
        return float(value)


def _is_public_evidence_text(value: str, maximum: int) -> bool:
    """校验可展示 Evidence 字段，拒绝不可见控制字符污染审计视图。"""
    return (
        1 <= len(value) <= maximum
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def build_default_observability_plan() -> RCAWorkflowPlan:
    """构造指标、变更、日志、链路和 Runbook 固定只读计划。"""
    return RCAWorkflowPlan(
        plan_id="default.observability-rca",
        version="v3",
        steps=(
            RCAWorkflowStep(
                step_id="collect.metrics",
                tool_name="metrics.query",
                tool_version="v1",
                payload={"window_minutes": 15, "max_series": 200},
            ),
            RCAWorkflowStep(
                step_id="collect.changes",
                tool_name="changes.query",
                tool_version="v1",
                payload={"max_results": 20},
            ),
            RCAWorkflowStep(
                step_id="collect.logs",
                tool_name="logs.query",
                tool_version="v1",
                payload={"window_minutes": 15, "limit": 500},
            ),
            RCAWorkflowStep(
                step_id="collect.traces",
                tool_name="traces.query",
                tool_version="v1",
                payload={"window_minutes": 15, "limit": 100},
            ),
            RCAWorkflowStep(
                step_id="retrieve.runbooks",
                tool_name="runbooks.retrieve",
                tool_version="v1",
                payload={"max_results": 5},
            ),
        ),
    )
