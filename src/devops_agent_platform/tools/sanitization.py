import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from devops_agent_platform.domain.exceptions import AppValidationError

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|password|passwd|pwd|token|access_token|"
    r"refresh_token|api[_-]?key|client[_-]?secret|secret|cookie|"
    r"session[_-]?id|credential|private[_-]?key)\b(\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|Bearer\s+[^\s,;]+|[^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT_TOKEN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}\b"
)
_EMAIL_ADDRESS = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_URI_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@/\s]+)@")
_KEY_SEPARATOR = re.compile(r"[^a-z0-9]+")
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "passwd",
        "password",
        "pwd",
        "secret",
        "token",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "client_secret",
        "connection_string",
        "dsn",
        "private_key",
        "set_cookie",
    }
)


def redact_sensitive_text(value: str) -> tuple[str, bool]:
    """遮蔽常见凭据、JWT和邮箱地址，并返回是否发生替换。"""
    original = value
    value = _SECRET_ASSIGNMENT.sub(
        lambda match: (f"{match.group(1)}{match.group(2)}[REDACTED]"),
        value,
    )
    value = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
    value = _JWT_TOKEN.sub("[REDACTED_JWT]", value)
    value = _URI_USERINFO.sub(
        lambda match: (f"{match.group(1)}[REDACTED]:[REDACTED]@"),
        value,
    )
    value = _EMAIL_ADDRESS.sub("[REDACTED_EMAIL]", value)
    return value, value != original


def truncate_utf8(
    value: str,
    maximum_bytes: int,
) -> tuple[str, bool]:
    """按UTF-8字节安全截断，确保结果不突破调用方上限。"""
    encoded = value.encode()
    if len(encoded) <= maximum_bytes:
        return value, False
    suffix = b"...[TRUNCATED]"
    if maximum_bytes <= len(suffix):
        return suffix[:maximum_bytes].decode(), True
    available = maximum_bytes - len(suffix)
    prefix = encoded[:available].decode(errors="ignore")
    return f"{prefix}{suffix.decode()}", True


@dataclass(frozen=True)
class EvidenceSanitizerConfig:
    """Evidence 持久化前的递归结构与文本容量边界。"""

    max_depth: int = 12
    max_collection_items: int = 500
    max_total_nodes: int = 5000
    max_string_bytes: int = 8192

    def __post_init__(self) -> None:
        """限制策略参数，防止错误配置关闭资源保护。"""
        self._validate("max_depth", self.max_depth, 64)
        self._validate(
            "max_collection_items",
            self.max_collection_items,
            10_000,
        )
        self._validate("max_total_nodes", self.max_total_nodes, 100_000)
        self._validate(
            "max_string_bytes",
            self.max_string_bytes,
            64 * 1024,
        )

    @staticmethod
    def _validate(field_name: str, value: int, maximum: int) -> None:
        """要求配置为有限正整数，并拒绝 bool 冒充整数。"""
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise AppValidationError(f"{field_name} must be between 1 and {maximum}")


@dataclass(frozen=True)
class SanitizedEvidencePayload:
    """脱敏后的独立载荷及策略处理统计。"""

    payload: dict[str, Any]
    redacted_values: int
    truncated_strings: int
    total_nodes: int


def sanitize_evidence_payload(
    payload: Mapping[str, Any],
    config: EvidenceSanitizerConfig | None = None,
) -> SanitizedEvidencePayload:
    """递归复制并清洗工具输出，不修改工具返回的原始对象。

    敏感字段整值替换，普通字符串执行文本脱敏和 UTF-8 安全截断。结构过深、
    节点过多或单集合过大时直接拒绝，避免以不完整数据冒充完整证据。
    """
    if not isinstance(payload, Mapping):
        raise AppValidationError("evidence payload must be a mapping")
    resolved_config = config or EvidenceSanitizerConfig()
    state = _SanitizationState()
    sanitized = _sanitize_value(
        payload,
        depth=0,
        config=resolved_config,
        state=state,
    )
    if not isinstance(sanitized, dict):
        raise AppValidationError("evidence payload must produce an object")
    return SanitizedEvidencePayload(
        payload=sanitized,
        redacted_values=state.redacted_values,
        truncated_strings=state.truncated_strings,
        total_nodes=state.total_nodes,
    )


@dataclass
class _SanitizationState:
    """单次递归清洗过程的内部可变计数器。"""

    redacted_values: int = 0
    truncated_strings: int = 0
    total_nodes: int = 0


def _sanitize_value(
    value: Any,
    *,
    depth: int,
    config: EvidenceSanitizerConfig,
    state: _SanitizationState,
) -> Any:
    """递归构造只包含标准 JSON 类型的新对象。"""
    if depth > config.max_depth:
        raise AppValidationError("evidence payload exceeds maximum depth")
    state.total_nodes += 1
    if state.total_nodes > config.max_total_nodes:
        raise AppValidationError("evidence payload contains too many nodes")

    if isinstance(value, Mapping):
        if len(value) > config.max_collection_items:
            raise AppValidationError("evidence object contains too many fields")
        sanitized_mapping: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AppValidationError("evidence object keys must be strings")
            if _is_sensitive_key(key):
                sanitized_mapping[key] = "[REDACTED]"
                state.redacted_values += 1
            else:
                sanitized_mapping[key] = _sanitize_value(
                    item,
                    depth=depth + 1,
                    config=config,
                    state=state,
                )
        return sanitized_mapping

    if isinstance(value, list | tuple):
        if len(value) > config.max_collection_items:
            raise AppValidationError("evidence array contains too many items")
        return [
            _sanitize_value(
                item,
                depth=depth + 1,
                config=config,
                state=state,
            )
            for item in value
        ]

    if isinstance(value, str):
        redacted, changed = redact_sensitive_text(value)
        sanitized_text, truncated = truncate_utf8(
            redacted,
            config.max_string_bytes,
        )
        state.redacted_values += int(changed)
        state.truncated_strings += int(truncated)
        return sanitized_text

    if value is None or isinstance(value, bool | int | float):
        return value
    raise AppValidationError("evidence payload contains a non-JSON value")


def _is_sensitive_key(key: str) -> bool:
    """按大小写无关字段名识别常见凭据容器。"""
    normalized = _KEY_SEPARATOR.sub("_", key.lower()).strip("_")
    if normalized in _SENSITIVE_KEYS:
        return True
    return bool(
        set(part for part in normalized.split("_") if part) & _SENSITIVE_KEY_PARTS
    )
