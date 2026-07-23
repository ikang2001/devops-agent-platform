import asyncio
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.logs import (
    LogsRangeQueryPort,
    LogsRangeResult,
    LogStream,
)
from devops_agent_platform.ports.observability import (
    ObservabilityTarget,
    ObservabilityTargetResolverPort,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.sanitization import (
    redact_sensitive_text,
    truncate_utf8,
)

Clock = Callable[[], datetime]

_LABEL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUPPORTED_SIGNALS = ("errors", "timeouts", "resource_pressure")


@dataclass(frozen=True)
class LokiLogsQueryHandlerConfig:
    """Logs工具的标签、时间窗、条目和脱敏输出配置。"""

    tenant_label: str = "tenant_id"
    service_label: str = "service"
    max_window_minutes: int = 60
    max_requested_entries: int = 500
    max_total_entries: int = 40
    max_output_line_bytes: int = 768
    max_output_label_bytes: int = 128
    allowed_output_labels: tuple[str, ...] = (
        "service",
        "namespace",
        "pod",
        "container",
        "level",
        "instance",
    )

    def __post_init__(self) -> None:
        """校验LogQL标签名和全部输出上界。"""
        for field_name in ("tenant_label", "service_label"):
            self._validate_label(field_name, getattr(self, field_name))
        for field_name, value, maximum in (
            ("max_window_minutes", self.max_window_minutes, 24 * 60),
            (
                "max_requested_entries",
                self.max_requested_entries,
                5000,
            ),
            ("max_total_entries", self.max_total_entries, 200),
            (
                "max_output_line_bytes",
                self.max_output_line_bytes,
                4096,
            ),
            (
                "max_output_label_bytes",
                self.max_output_label_bytes,
                1024,
            ),
        ):
            self._validate_positive_int(field_name, value, maximum)
        if self.max_total_entries > self.max_requested_entries:
            raise AppValidationError(
                "max_total_entries must not exceed max_requested_entries"
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
            self._validate_label("allowed output label", label)

    @staticmethod
    def _validate_label(field_name: str, value: str) -> None:
        if (
            not isinstance(value, str)
            or len(value) > 128
            or _LABEL_IDENTIFIER.fullmatch(value) is None
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


class LokiLogsQueryHandler:
    """执行固定LogQL模板并输出脱敏、截断的有限日志。"""

    def __init__(
        self,
        target_resolver: ObservabilityTargetResolverPort,
        range_query: LogsRangeQueryPort,
        config: LokiLogsQueryHandlerConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        """创建Handler，不在内部保存业务状态或网络连接。"""
        if not callable(getattr(target_resolver, "resolve", None)):
            raise AppValidationError(
                "target_resolver must provide resolve"
            )
        if not callable(getattr(range_query, "query_range", None)):
            raise AppValidationError(
                "range_query must provide query_range"
            )
        if config is not None and not isinstance(
            config,
            LokiLogsQueryHandlerConfig,
        ):
            raise AppValidationError(
                "config must be a LokiLogsQueryHandlerConfig"
            )
        if clock is not None and not callable(clock):
            raise AppValidationError("clock must be callable")
        self._target_resolver = target_resolver
        self._range_query = range_query
        self._config = config or LokiLogsQueryHandlerConfig()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(
        self,
        payload: Mapping[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        """解析可信目标并并发执行有限日志模板。"""
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
            self._config.max_total_entries,
        )
        if total_limit < len(signals):
            raise AppValidationError(
                "limit must be at least the number of signals"
            )
        per_signal_limit = total_limit // len(signals)
        templates = self._build_templates(target)
        tasks = [
            asyncio.create_task(
                self._range_query.query_range(
                    tenant_id=target.tenant_id,
                    query=templates[signal],
                    start=start,
                    end=end,
                    limit=per_signal_limit,
                    trace_id=trace_id,
                ),
                name=f"logs-query-{signal}",
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
            "source": "loki",
            "target": {
                "tenant_id": target.tenant_id,
                "incident_id": target.incident_id,
                "service_name": target.service_name,
            },
            "window": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "direction": "backward",
            },
            "signals": [
                self._summarize_signal(signal, result)
                for signal, result in zip(signals, results, strict=True)
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
        """只接受工作流身份和有限日志查询控制参数。"""
        if not isinstance(payload, Mapping):
            raise AppValidationError("logs payload must be a mapping")
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
                "logs payload contains unsupported fields: "
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
            self._config.max_requested_entries,
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
        """使用转义标签值构造固定LogQL模板。"""
        tenant = self._escape_label_value(target.tenant_id)
        service = self._escape_label_value(target.service_name)
        selector = (
            f'{{{self._config.tenant_label}="{tenant}",'
            f'{self._config.service_label}="{service}"}}'
        )
        return {
            "errors": (
                f'{selector} |~ "(?i)(error|exception|fatal|panic)"'
            ),
            "timeouts": (
                f'{selector} |~ "(?i)(timeout|deadline exceeded|'
                'context deadline)"'
            ),
            "resource_pressure": (
                f'{selector} |~ "(?i)(oomkilled|out of memory|'
                'crashloop|evicted|killed)"'
            ),
        }

    def _summarize_signal(
        self,
        signal: str,
        result: LogsRangeResult,
    ) -> dict[str, Any]:
        """扁平化日志流并按纳秒时间倒序输出。"""
        entries: list[dict[str, Any]] = []
        for stream in result.streams:
            entries.extend(self._sanitize_stream(stream))
        entries.sort(
            key=lambda entry: int(entry["timestamp_ns"]),
            reverse=True,
        )
        return {
            "name": signal,
            "entries": entries,
            "entry_count": len(entries),
            "possibly_truncated": result.possibly_truncated,
        }

    def _sanitize_stream(
        self,
        stream: LogStream,
    ) -> list[dict[str, Any]]:
        """对白名单标签和每条日志执行脱敏与字节截断。"""
        labels = {
            name: self._truncate_utf8(
                self._redact(value)[0],
                self._config.max_output_label_bytes,
            )[0]
            for name, value in stream.labels
            if (
                name in self._config.allowed_output_labels
                or name == self._config.service_label
            )
        }
        sanitized: list[dict[str, Any]] = []
        for timestamp_ns, line in stream.entries:
            redacted_line, redacted = self._redact(line)
            single_line = (
                redacted_line.replace("\r", "\\r")
                .replace("\n", "\\n")
                .replace("\t", "\\t")
            )
            output_line, truncated = self._truncate_utf8(
                single_line,
                self._config.max_output_line_bytes,
            )
            sanitized.append(
                {
                    "timestamp_ns": timestamp_ns,
                    "line": output_line,
                    "labels": labels,
                    "redacted": redacted,
                    "truncated": truncated,
                }
            )
        return sanitized

    @staticmethod
    def _redact(value: str) -> tuple[str, bool]:
        """遮蔽常见凭据、JWT和邮箱地址。"""
        return redact_sensitive_text(value)

    @staticmethod
    def _truncate_utf8(
        value: str,
        maximum_bytes: int,
    ) -> tuple[str, bool]:
        """按UTF-8字节安全截断，避免切断多字节字符。"""
        return truncate_utf8(value, maximum_bytes)

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
        """按LogQL字符串规则转义标签值。"""
        return (
            value.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace('"', '\\"')
        )


def register_loki_logs_tool(
    registry: ToolHandlerRegistry,
    handler: LokiLogsQueryHandler,
    *,
    timeout_ms: int = 5000,
) -> ToolDefinition:
    """把logs.query@v1定义和处理器绑定到注册表。"""
    definition = ToolDefinition(
        tool_name="logs.query",
        version="v1",
        risk_level=ToolRiskLevel.MEDIUM,
        timeout_ms=timeout_ms,
        permission_tags=("logs:read", "tenant:observe"),
    )
    registry.register(definition, handler)
    return definition
