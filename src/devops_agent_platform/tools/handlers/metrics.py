import asyncio
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.metrics import (
    MetricSeries,
    MetricsRangeQueryPort,
    MetricsRangeResult,
    MetricsTarget,
    MetricsTargetResolverPort,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry

Clock = Callable[[], datetime]
_PROMETHEUS_METRIC_IDENTIFIER = re.compile(
    r"^[A-Za-z_:][A-Za-z0-9_:]*$"
)
_PROMETHEUS_LABEL_IDENTIFIER = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)
_SUPPORTED_SIGNALS = (
    "availability",
    "request_rate",
    "error_ratio",
    "latency_p95",
)


@dataclass(frozen=True)
class PrometheusMetricsQueryHandlerConfig:
    """Metrics工具的模板、时间窗和输出容量配置。"""

    tenant_label: str = "tenant_id"
    service_label: str = "service"
    status_label: str = "status"
    requests_metric: str = "http_requests_total"
    latency_bucket_metric: str = "http_request_duration_seconds_bucket"
    availability_metric: str = "up"
    max_window_minutes: int = 60
    max_requested_series: int = 200
    max_total_series: int = 50
    max_points_per_series: int = 120
    min_step_seconds: int = 15
    allowed_output_labels: tuple[str, ...] = (
        "service",
        "instance",
        "job",
        "namespace",
        "pod",
    )

    def __post_init__(self) -> None:
        """校验PromQL标识符和全部容量上界。"""
        for field_name in (
            "tenant_label",
            "service_label",
            "status_label",
        ):
            self._validate_identifier(
                field_name,
                getattr(self, field_name),
                _PROMETHEUS_LABEL_IDENTIFIER,
            )
        for field_name in (
            "requests_metric",
            "latency_bucket_metric",
            "availability_metric",
        ):
            self._validate_identifier(
                field_name,
                getattr(self, field_name),
                _PROMETHEUS_METRIC_IDENTIFIER,
            )
        for field_name, value, maximum in (
            ("max_window_minutes", self.max_window_minutes, 24 * 60),
            (
                "max_requested_series",
                self.max_requested_series,
                1000,
            ),
            ("max_total_series", self.max_total_series, 200),
            (
                "max_points_per_series",
                self.max_points_per_series,
                1000,
            ),
            ("min_step_seconds", self.min_step_seconds, 3600),
        ):
            self._validate_positive_int(field_name, value, maximum)
        if self.max_total_series > self.max_requested_series:
            raise AppValidationError(
                "max_total_series must not exceed max_requested_series"
            )
        if (
            not isinstance(self.allowed_output_labels, tuple)
            or len(self.allowed_output_labels) > 50
            or len(set(self.allowed_output_labels))
            != len(self.allowed_output_labels)
        ):
            raise AppValidationError(
                "allowed_output_labels is invalid"
            )
        for label in self.allowed_output_labels:
            self._validate_identifier(
                "allowed output label",
                label,
                _PROMETHEUS_LABEL_IDENTIFIER,
            )

    @staticmethod
    def _validate_identifier(
        field_name: str,
        value: str,
        pattern: re.Pattern[str],
    ) -> None:
        if (
            not isinstance(value, str)
            or len(value) > 128
            or pattern.fullmatch(value) is None
        ):
            raise AppValidationError(f"{field_name} is invalid")

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


class PrometheusMetricsQueryHandler:
    """执行固定PromQL模板并返回有界趋势摘要。"""

    def __init__(
        self,
        target_resolver: MetricsTargetResolverPort,
        range_query: MetricsRangeQueryPort,
        config: PrometheusMetricsQueryHandlerConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        """创建Handler，不在内部维护业务目标或网络会话。"""
        if not callable(getattr(target_resolver, "resolve", None)):
            raise AppValidationError(
                "target_resolver must provide resolve"
            )
        if not callable(getattr(range_query, "query_range", None)):
            raise AppValidationError(
                "range_query must provide query_range"
            )
        if clock is not None and not callable(clock):
            raise AppValidationError("clock must be callable")
        if config is not None and not isinstance(
            config,
            PrometheusMetricsQueryHandlerConfig,
        ):
            raise AppValidationError(
                "config must be a PrometheusMetricsQueryHandlerConfig"
            )
        self._target_resolver = target_resolver
        self._range_query = range_query
        self._config = config or PrometheusMetricsQueryHandlerConfig()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        """解析可信目标并并发执行有限黄金信号模板。"""
        request = self._parse_payload(payload)
        target = await self._target_resolver.resolve(
            request["tenant_id"],
            request["incident_id"],
        )
        end = self._now()
        start = end - timedelta(minutes=request["window_minutes"])
        step_seconds = max(
            self._config.min_step_seconds,
            math.ceil(
                request["window_minutes"]
                * 60
                / self._config.max_points_per_series
            ),
        )
        signals = request["signals"]
        total_series_limit = min(
            request["max_series"],
            self._config.max_total_series,
        )
        if total_series_limit < len(signals):
            raise AppValidationError(
                "max_series must be at least the number of signals"
            )
        series_limit = total_series_limit // len(signals)
        samples_limit = (
            math.floor(
                request["window_minutes"] * 60 / step_seconds
            )
            + 2
        )
        templates = self._build_templates(
            target,
            request["window_minutes"],
        )
        tasks = [
            asyncio.create_task(
                self._range_query.query_range(
                    tenant_id=target.tenant_id,
                    query=templates[signal],
                    start=start,
                    end=end,
                    step_seconds=step_seconds,
                    series_limit=series_limit,
                    samples_per_series_limit=samples_limit,
                    trace_id=trace_id,
                ),
                name=f"metrics-query-{signal}",
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
        signal_results = [
            self._summarize_signal(signal, result)
            for signal, result in zip(signals, results, strict=True)
        ]
        return {
            "source": "prometheus",
            "target": {
                "tenant_id": target.tenant_id,
                "incident_id": target.incident_id,
                "service_name": target.service_name,
            },
            "window": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step_seconds": step_seconds,
            },
            "signals": signal_results,
            "requested_max_series": request["max_series"],
            "effective_max_series": (
                series_limit * len(signals)
            ),
            "possibly_truncated": any(
                item.possibly_truncated for item in results
            ),
        }

    def _parse_payload(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """只接受工作流注入身份和有限查询控制参数。"""
        if not isinstance(payload, Mapping):
            raise AppValidationError(
                "metrics payload must be a mapping"
            )
        allowed_fields = {
            "tenant_id",
            "incident_id",
            "window_minutes",
            "max_series",
            "signals",
            # 其余字段由受控工作流注入，Handler不参与查询决策。
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
                "metrics payload contains unsupported fields: "
                f"{', '.join(unknown_fields)}"
            )
        tenant_id = self._required_text(payload, "tenant_id", 128)
        incident_id = self._required_text(payload, "incident_id", 64)
        window_minutes = self._bounded_int(
            payload,
            "window_minutes",
            self._config.max_window_minutes,
        )
        max_series = self._bounded_int(
            payload,
            "max_series",
            self._config.max_requested_series,
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
            "max_series": max_series,
            "signals": tuple(raw_signals),
        }

    def _build_templates(
        self,
        target: MetricsTarget,
        window_minutes: int,
    ) -> dict[str, str]:
        """使用转义标签值构造固定PromQL模板。"""
        tenant = self._escape_label_value(target.tenant_id)
        service = self._escape_label_value(target.service_name)
        base_selector = (
            f'{self._config.tenant_label}="{tenant}",'
            f'{self._config.service_label}="{service}"'
        )
        group = self._config.service_label
        rate_window = min(window_minutes, 5)
        requests = self._config.requests_metric
        rate_expression = (
            f'rate({requests}{{{base_selector}}}'
            f'[{rate_window}m])'
        )
        error_expression = (
            f'rate({requests}{{{base_selector},'
            f'{self._config.status_label}=~"5.."}}'
            f'[{rate_window}m])'
        )
        latency = self._config.latency_bucket_metric
        return {
            "availability": (
                f"avg by ({group}) "
                f"({self._config.availability_metric}"
                f"{{{base_selector}}})"
            ),
            "request_rate": (
                f"sum by ({group}) ({rate_expression})"
            ),
            "error_ratio": (
                f"sum by ({group}) ({error_expression}) "
                f"/ clamp_min(sum by ({group}) "
                f"({rate_expression}), 1)"
            ),
            "latency_p95": (
                "histogram_quantile(0.95, "
                f"sum by (le, {group}) "
                f"(rate({latency}{{{base_selector}}}"
                f"[{rate_window}m])))"
            ),
        }

    def _summarize_signal(
        self,
        signal: str,
        result: MetricsRangeResult,
    ) -> dict[str, Any]:
        """把矩阵序列压缩为有限统计摘要。"""
        return {
            "name": signal,
            "series": [
                self._summarize_series(series)
                for series in result.series
            ],
            "warnings": list(result.warnings),
            "possibly_truncated": result.possibly_truncated,
        }

    def _summarize_series(
        self,
        series: MetricSeries,
    ) -> dict[str, Any]:
        """汇总有限值，并把NaN和无穷值计入异常样本数。"""
        finite_samples: list[tuple[float, float]] = []
        non_finite_samples = 0
        for timestamp, raw_value in series.samples:
            try:
                value = float(raw_value)
            except ValueError:
                non_finite_samples += 1
                continue
            if not math.isfinite(timestamp) or not math.isfinite(value):
                non_finite_samples += 1
                continue
            finite_samples.append((timestamp, value))

        labels = {
            name: value
            for name, value in series.labels
            if (
                name in self._config.allowed_output_labels
                or name == self._config.service_label
            )
        }
        if not finite_samples:
            return {
                "labels": labels,
                "sample_count": len(series.samples),
                "finite_sample_count": 0,
                "non_finite_sample_count": non_finite_samples,
                "first": None,
                "latest": None,
                "minimum": None,
                "maximum": None,
                "average": None,
                "change": None,
            }
        finite_samples.sort(key=lambda item: item[0])
        values = [value for _, value in finite_samples]
        first = finite_samples[0]
        latest = finite_samples[-1]
        return {
            "labels": labels,
            "sample_count": len(series.samples),
            "finite_sample_count": len(finite_samples),
            "non_finite_sample_count": non_finite_samples,
            "first": {
                "timestamp": first[0],
                "value": first[1],
            },
            "latest": {
                "timestamp": latest[0],
                "value": latest[1],
            },
            "minimum": min(values),
            "maximum": max(values),
            "average": sum(values) / len(values),
            "change": latest[1] - first[1],
        }

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
    def _escape_label_value(value: str) -> str:
        """按PromQL字符串规则转义标签值，阻断选择器注入。"""
        return (
            value.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace('"', '\\"')
        )


def register_prometheus_metrics_tool(
    registry: ToolHandlerRegistry,
    handler: PrometheusMetricsQueryHandler,
    *,
    timeout_ms: int = 5000,
) -> ToolDefinition:
    """把metrics.query@v1定义和处理器原子绑定到注册表。"""
    definition = ToolDefinition(
        tool_name="metrics.query",
        version="v1",
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=timeout_ms,
        permission_tags=("metrics:read", "tenant:observe"),
    )
    registry.register(definition, handler)
    return definition
