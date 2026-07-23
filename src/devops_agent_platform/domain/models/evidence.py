import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from devops_agent_platform.domain.enums import EvidenceType
from devops_agent_platform.domain.exceptions import AppValidationError

MAX_EVIDENCE_CONTENT_BYTES = 64 * 1024


@dataclass(frozen=True)
class Evidence:
    """绑定到单个 RCA 工作流执行代次的不可变结构化证据。

    Evidence 是后续审计、重放和报告生成的基础事实，因此必须携带租户、
    工作流、执行代次和工具来源。这样即使执行器失联后被其它 Worker 接管，
    旧执行器迟到写入的证据也能被唯一约束挡住或被查询侧识别。
    """

    evidence_id: str
    tenant_id: str
    incident_id: str
    workflow_run_id: str
    execution_attempt: int
    step_id: str
    tool_name: str
    tool_version: str
    evidence_type: EvidenceType
    source: str
    summary: str
    content_json: str
    content_sha256: str
    confidence: float
    collected_at: datetime

    def __post_init__(self) -> None:
        """校验证据事实，阻止工具返回的脏数据越过应用层直接落库。"""
        self._validate_text("evidence_id", self.evidence_id, 64)
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("incident_id", self.incident_id, 64)
        self._validate_text("workflow_run_id", self.workflow_run_id, 64)
        self._validate_positive_int("execution_attempt", self.execution_attempt)
        self._validate_text("step_id", self.step_id, 128)
        self._validate_text("tool_name", self.tool_name, 128)
        self._validate_text("tool_version", self.tool_version, 64)
        self._validate_text("source", self.source, 128)
        self._validate_text("summary", self.summary, 4096)
        self._validate_confidence(self.confidence)
        self._validate_timestamp("collected_at", self.collected_at)

        if not isinstance(self.evidence_type, EvidenceType):
            raise AppValidationError("evidence_type must be an EvidenceType")
        self._validate_content_integrity(self.content_json, self.content_sha256)

    @staticmethod
    def _validate_text(field_name: str, value: str, max_length: int) -> None:
        """校验必填文本，避免空值、超长值和首尾空白进入索引字段。"""
        if not isinstance(value, str):
            raise AppValidationError(f"{field_name} must be a string")
        if not 1 <= len(value) <= max_length:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {max_length}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )

    @staticmethod
    def _validate_positive_int(field_name: str, value: int) -> None:
        """校验执行代次这类 fencing 字段，拒绝 bool 冒充 int。"""
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise AppValidationError(f"{field_name} must be a positive integer")

    @staticmethod
    def _validate_confidence(value: float) -> None:
        """把置信度限制在稳定区间内，方便后续排序和阈值过滤。"""
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise AppValidationError("confidence must be a number")
        if not 0 <= float(value) <= 1:
            raise AppValidationError("confidence must be between 0 and 1")

    @staticmethod
    def _validate_timestamp(field_name: str, value: datetime) -> None:
        """要求采集时间携带时区，避免跨部署环境比较结果漂移。"""
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(f"{field_name} must include timezone information")

    @staticmethod
    def _validate_content_integrity(content_json: str, content_sha256: str) -> None:
        """校验内容是有限大小的 JSON 对象，并且摘要与内容完全匹配。"""
        if not isinstance(content_json, str) or not content_json:
            raise AppValidationError("content_json must be a non-empty string")
        encoded = content_json.encode("utf-8")
        if len(encoded) > MAX_EVIDENCE_CONTENT_BYTES:
            raise AppValidationError("content_json is too large")

        try:
            payload = json.loads(
                content_json,
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError) as exc:
            raise AppValidationError("content_json must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise AppValidationError("content_json must be a JSON object")

        if (
            not isinstance(content_sha256, str)
            or len(content_sha256) != 64
            or any(char not in "0123456789abcdef" for char in content_sha256)
        ):
            raise AppValidationError("content_sha256 must be a SHA-256 hex digest")
        actual_sha256 = hashlib.sha256(encoded).hexdigest()
        if actual_sha256 != content_sha256:
            raise AppValidationError("content_sha256 does not match content_json")


def build_evidence_content(
    payload: Mapping[str, Any],
    *,
    max_bytes: int = MAX_EVIDENCE_CONTENT_BYTES,
) -> tuple[str, str]:
    """把工具证据载荷转换为可幂等校验的规范 JSON 和 SHA-256。

    调用方不要直接把 dict 转字符串落库。这里统一排序键、去掉多余空格并禁止
    NaN/Infinity，保证相同内容在不同进程中得到相同哈希。
    """
    if not isinstance(payload, Mapping) or not payload:
        raise AppValidationError("payload must be a non-empty mapping")
    try:
        content_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AppValidationError("payload must be JSON serializable") from exc

    encoded = content_json.encode("utf-8")
    if len(encoded) > max_bytes:
        raise AppValidationError("payload is too large")
    return content_json, hashlib.sha256(encoded).hexdigest()


def _reject_json_constant(value: str) -> None:
    """拒绝 JSON 标准之外的 NaN/Infinity 常量。"""
    raise ValueError(f"invalid JSON constant: {value}")
