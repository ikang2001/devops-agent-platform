import json
import logging
import re
import traceback
from datetime import UTC, datetime
from typing import Any

from devops_agent_platform.infrastructure.logging.context import (
    get_current_trace_id,
)

_OPTIONAL_FIELDS = (
    "event",
    "http_method",
    "http_path",
    "http_status_code",
    "duration_ms",
    "worker_id",
    "consecutive_failures",
)
_MESSAGE_LIMIT = 4096
_EXCEPTION_MESSAGE_LIMIT = 2048
_STACK_TRACE_LIMIT = 16384
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(password|passwd|token|access_token|api_key|secret|authorization)"
    r"(\s*[:=]\s*)([^\s,;&]+)"
)
_URI_CREDENTIAL_PATTERN = re.compile(r"(://[^:/@\s]+:)([^@\s]+)(@)")


class JsonLogFormatter(logging.Formatter):
    """把日志转换为字段稳定、可被采集平台解析的单行JSON。"""

    def __init__(self, service_name: str, environment: str) -> None:
        super().__init__()
        self._service_name = service_name
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        """仅输出白名单字段，避免任意extra对象和敏感上下文外泄。"""
        payload: dict[str, Any] = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": self._sanitize(record.getMessage(), _MESSAGE_LIMIT),
            "service": self._service_name,
            "environment": self._environment,
        }
        trace_id = getattr(record, "trace_id", None) or get_current_trace_id()
        if trace_id is not None:
            payload["trace_id"] = self._sanitize(str(trace_id), 128)

        for field_name in _OPTIONAL_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = self._normalize_value(value)

        if record.exc_info is not None:
            payload["exception"] = self._format_exception(record.exc_info)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _format_timestamp(created: float) -> str:
        """生成UTC、毫秒精度且时区明确的时间戳。"""
        return (
            datetime.fromtimestamp(created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        """限制单字段大小，避免异常文本导致日志事件失控。"""
        return value if len(value) <= limit else f"{value[:limit]}...[truncated]"

    @classmethod
    def _sanitize(cls, value: str, limit: int) -> str:
        """遮蔽常见凭据表达式，并限制最终字段大小。"""
        redacted = _SENSITIVE_ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
            value,
        )
        redacted = _URI_CREDENTIAL_PATTERN.sub(
            r"\1[REDACTED]\3",
            redacted,
        )
        return cls._truncate(redacted, limit)

    @classmethod
    def _normalize_value(cls, value: Any) -> Any:
        """保留JSON原生标量，其余类型转换为有界字符串。"""
        if isinstance(value, str):
            return cls._sanitize(value, _MESSAGE_LIMIT)
        if value is None or isinstance(value, bool | int | float):
            return value
        return cls._sanitize(str(value), _MESSAGE_LIMIT)

    @classmethod
    def _format_exception(
        cls,
        exc_info: tuple[type[BaseException], BaseException, object | None],
    ) -> dict[str, str]:
        """输出有界异常摘要和堆栈，供线上定位但不进入API响应。"""
        exception_type, exception, exception_traceback = exc_info
        stack_trace = "".join(
            traceback.format_exception(
                exception_type,
                exception,
                exception_traceback,
            )
        )
        return {
            "type": exception_type.__name__,
            "message": cls._sanitize(
                str(exception),
                _EXCEPTION_MESSAGE_LIMIT,
            ),
            "stack_trace": cls._sanitize(stack_trace, _STACK_TRACE_LIMIT),
        }
