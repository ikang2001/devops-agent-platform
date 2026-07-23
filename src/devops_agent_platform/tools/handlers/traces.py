import asyncio
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.observability import (
    ObservabilityTarget,
    ObservabilityTargetResolverPort,
)
from devops_agent_platform.ports.traces import (
    TraceSearchPort,
    TraceSearchResult,
    TraceSummary,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.sanitization import (
    redact_sensitive_text,
    truncate_utf8,
)

Clock = Callable[[], datetime]
_SUPPORTED_SIGNALS = ("errors", "slow_spans")
_TRACEQL_ATTRIBUTE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.-]*$"
)


@dataclass(frozen=True)
class TempoTracesQueryHandlerConfig:
    """Traces工具的模板、时间窗、条目和安全输出配置。"""

    service_attribute: str = "resource.service.name"
    max_window_minutes: int = 60
    max_requested_traces: int = 100
    max_total_traces: int = 20
    spans_per_span_set: int = 1
    slow_span_threshold_ms: int = 1000
    max_output_name_bytes: int = 256

    def __post_init__(self) -> None:
        """校验TraceQL属性名、阈值和全部输出上界。"""
        if (
            not isinstance(self.service_attribute, str)
            or len(self.service_attribute) > 128
            or _TRACEQL_ATTRIBUTE.fullmatch(
                self.service_attribute
            )
            is None
        ):
            raise AppValidationError("service_attribute is invalid")
        for field_name, value, maximum in (
            ("max_window_minutes", self.max_window_minutes, 24 * 60),
            (
                "max_requested_traces",
                self.max_requested_traces,
                1000,
            ),
            ("max_total_traces", self.max_total_traces, 200),
            ("spans_per_span_set", self.spans_per_span_set, 10),
            (
                "slow_span_threshold_ms",
                self.slow_span_threshold_ms,
                10 * 60 * 1000,
            ),
            (
                "max_output_name_bytes",
                self.max_output_name_bytes,
                4096,
            ),
        ):
            self._validate_positive_int(field_name, value, maximum)
        if self.max_total_traces > self.max_requested_traces:
            raise AppValidationError(
                "max_total_traces must not exceed max_requested_traces"
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
            raise AppValidationError(
                f"{field_name} must be between 1 and {maximum}"
            )


class TempoTracesQueryHandler:
    """执行固定TraceQL模板并输出脱敏、截断的Trace摘要。"""

    def __init__(
        self,
        target_resolver: ObservabilityTargetResolverPort,
        trace_search: TraceSearchPort,
        config: TempoTracesQueryHandlerConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        """创建Handler，不在内部保存业务状态或网络连接。"""
        if not callable(getattr(target_resolver, "resolve", None)):
            raise AppValidationError(
                "target_resolver must provide resolve"
            )
        if not callable(getattr(trace_search, "search", None)):
            raise AppValidationError(
                "trace_search must provide search"
            )
        if config is not None and not isinstance(
            config,
            TempoTracesQueryHandlerConfig,
        ):
            raise AppValidationError(
                "config must be a TempoTracesQueryHandlerConfig"
            )
        if clock is not None and not callable(clock):
            raise AppValidationError("clock must be callable")
        self._target_resolver = target_resolver
        self._trace_search = trace_search
        self._config = config or TempoTracesQueryHandlerConfig()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        """解析可信目标并并发执行有限Trace搜索模板。"""
        request = self._parse_payload(payload)
        target = await self._target_resolver.resolve(
            request["tenant_id"],
            request["incident_id"],
        )
        end = self._now()
        start = end - timedelta(minutes=request["window_minutes"])
        signals = request["signals"]
        total_limit = min(
            request["limit"],
            self._config.max_total_traces,
        )
        if total_limit < len(signals):
            raise AppValidationError(
                "limit must be at least the number of signals"
            )
        per_signal_limit = total_limit // len(signals)
        templates = self._build_templates(target)
        tasks = [
            asyncio.create_task(
                self._trace_search.search(
                    tenant_id=target.tenant_id,
                    query=templates[signal],
                    start=start,
                    end=end,
                    limit=per_signal_limit,
                    spans_per_span_set=(
                        self._config.spans_per_span_set
                    ),
                    trace_id=trace_id,
                ),
                name=f"traces-query-{signal}",
            )
            for signal in signals
        ]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return {
            "source": "tempo",
            "target": {
                "tenant_id": target.tenant_id,
                "incident_id": target.incident_id,
                "service_name": target.service_name,
            },
            "window": {
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            "signals": [
                self._summarize_signal(signal, result)
                for signal, result in zip(
                    signals,
                    results,
                    strict=True,
                )
            ],
            "requested_limit": request["limit"],
            "effective_limit": per_signal_limit * len(signals),
            "possibly_truncated": any(
                result.possibly_truncated for result in results
            ),
        }

    def _parse_payload(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """只接受工作流身份和有限Trace搜索控制参数。"""
        if not isinstance(payload, Mapping):
            raise AppValidationError("traces payload must be a mapping")
        allowed_fields = {
            "tenant_id",
            "incident_id",
            "window_minutes",
            "limit",
            "signals",
            "execution_attempt",
            "operator_id",
            "plan_id",
            "plan_version",
            "step_id",
            "trace_id",
            "worker_id",
            "workflow_run_id",
        }
        unknown_fields = sorted(set(payload).difference(allowed_fields))
        if unknown_fields:
            raise AppValidationError(
                "traces payload contains unsupported fields: "
                f"{', '.join(unknown_fields)}"
            )
        tenant_id = self._required_text(payload, "tenant_id", 128)
        incident_id = self._required_text(payload, "incident_id", 64)
        window_minutes = self._bounded_int(
            payload,
            "window_minutes",
            self._config.max_window_minutes,
        )
        limit = self._bounded_int(
            payload,
            "limit",
            self._config.max_requested_traces,
        )
        raw_signals = payload.get("signals", list(_SUPPORTED_SIGNALS))
        if (
            not isinstance(raw_signals, list)
            or not raw_signals
            or len(raw_signals) > len(_SUPPORTED_SIGNALS)
            or not all(isinstance(item, str) for item in raw_signals)
            or len(set(raw_signals)) != len(raw_signals)
            or not set(raw_signals).issubset(_SUPPORTED_SIGNALS)
        ):
            raise AppValidationError("signals is invalid")
        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "window_minutes": window_minutes,
            "limit": limit,
            "signals": tuple(raw_signals),
        }

    def _build_templates(
        self,
        target: ObservabilityTarget,
    ) -> dict[str, str]:
        """使用转义服务名构造固定TraceQL模板。"""
        service = self._escape_traceql_string(target.service_name)
        prefix = (
            f'{{ {self._config.service_attribute} = "{service}" && '
        )
        suffix = " } with (most_recent=true)"
        return {
            "errors": f"{prefix}span:status = error{suffix}",
            "slow_spans": (
                f"{prefix}span:duration > "
                f"{self._config.slow_span_threshold_ms}ms{suffix}"
            ),
        }

    def _summarize_signal(
        self,
        signal: str,
        result: TraceSearchResult,
    ) -> dict[str, Any]:
        """按开始时间倒序输出有限Trace摘要和扫描成本。"""
        traces = [
            self._sanitize_trace(trace) for trace in result.traces
        ]
        traces.sort(
            key=lambda item: int(item["start_time_unix_nano"]),
            reverse=True,
        )
        return {
            "name": signal,
            "traces": traces,
            "trace_count": len(traces),
            "search_cost": {
                "inspected_traces": result.inspected_traces,
                "inspected_bytes": result.inspected_bytes,
            },
            "possibly_truncated": result.possibly_truncated,
        }

    def _sanitize_trace(
        self,
        trace: TraceSummary,
    ) -> dict[str, Any]:
        """清洗Trace名称，禁止控制字符和敏感文本进入Agent。"""
        root_service_name, service_redacted, service_truncated = (
            self._sanitize_name(trace.root_service_name)
        )
        root_trace_name, trace_redacted, trace_truncated = (
            self._sanitize_name(trace.root_trace_name)
        )
        return {
            "trace_id": trace.trace_id,
            "root_service_name": root_service_name,
            "root_trace_name": root_trace_name,
            "start_time_unix_nano": trace.start_time_unix_nano,
            "duration_ms": trace.duration_ms,
            "matched_spans": trace.matched_spans,
            "redacted": service_redacted or trace_redacted,
            "truncated": service_truncated or trace_truncated,
        }

    def _sanitize_name(self, value: str) -> tuple[str, bool, bool]:
        """脱敏、单行化并按UTF-8字节限制名称。"""
        redacted_value, redacted = redact_sensitive_text(value)
        single_line = (
            redacted_value.replace("\r", "\\r")
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        )
        output, truncated = truncate_utf8(
            single_line,
            self._config.max_output_name_bytes,
        )
        return output, redacted, truncated

    def _now(self) -> datetime:
        """读取并校验带时区应用时钟。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(
                "clock must return a timezone-aware datetime"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _required_text(
        payload: Mapping[str, Any],
        field_name: str,
        maximum: int,
    ) -> str:
        value = payload.get(field_name)
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
        ):
            raise AppValidationError(f"{field_name} is invalid")
        return value

    @staticmethod
    def _bounded_int(
        payload: Mapping[str, Any],
        field_name: str,
        maximum: int,
    ) -> int:
        value = payload.get(field_name)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise AppValidationError(
                f"{field_name} must be between 1 and {maximum}"
            )
        return value

    @staticmethod
    def _escape_traceql_string(value: str) -> str:
        """按TraceQL字符串规则转义服务名。"""
        return (
            value.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace('"', '\\"')
        )


def register_tempo_traces_tool(
    registry: ToolHandlerRegistry,
    handler: TempoTracesQueryHandler,
    *,
    timeout_ms: int = 6000,
) -> ToolDefinition:
    """把traces.query@v1定义和处理器绑定到注册表。"""
    definition = ToolDefinition(
        tool_name="traces.query",
        version="v1",
        risk_level=ToolRiskLevel.MEDIUM,
        timeout_ms=timeout_ms,
        permission_tags=("traces:read", "tenant:observe"),
    )
    registry.register(definition, handler)
    return definition
