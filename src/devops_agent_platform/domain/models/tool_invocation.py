import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from devops_agent_platform.domain.enums import (
    ToolInvocationStatus,
    ToolRiskLevel,
)
from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class ToolInvocation:
    """绑定到单个工作流执行代次的不可变工具调用审计记录。

    记录只保存输入、输出摘要及内容哈希，不保存原始请求参数，避免查询语句、
    凭据或日志正文被复制到审计表。原始工具输出由 Evidence 按容量上限承载。
    """

    invocation_id: str
    tenant_id: str
    incident_id: str
    workflow_run_id: str
    execution_attempt: int
    step_id: str
    operator_id: str
    trace_id: str
    tool_name: str
    tool_version: str
    risk_level: ToolRiskLevel
    status: ToolInvocationStatus
    input_summary: str
    input_sha256: str
    output_summary: str | None
    output_sha256: str | None
    latency_ms: int
    error_code: str | None
    started_at: datetime
    ended_at: datetime

    def __post_init__(self) -> None:
        """校验审计事实及成功、失败字段之间的一致性。"""
        required_text = (
            ("invocation_id", self.invocation_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("incident_id", self.incident_id, 64),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("step_id", self.step_id, 128),
            ("operator_id", self.operator_id, 128),
            ("trace_id", self.trace_id, 128),
            ("tool_name", self.tool_name, 128),
            ("tool_version", self.tool_version, 64),
            ("input_summary", self.input_summary, 256),
        )
        for field_name, value, maximum in required_text:
            self._validate_text(field_name, value, maximum)

        if (
            isinstance(self.execution_attempt, bool)
            or not isinstance(self.execution_attempt, int)
            or self.execution_attempt < 1
        ):
            raise AppValidationError("execution_attempt must be a positive integer")
        if not isinstance(self.risk_level, ToolRiskLevel):
            raise AppValidationError("risk_level must be a ToolRiskLevel")
        if self.status not in {
            ToolInvocationStatus.SUCCEEDED,
            ToolInvocationStatus.FAILED,
        }:
            raise AppValidationError(
                "status must be SUCCEEDED or FAILED for persisted invocation"
            )
        self._validate_sha256("input_sha256", self.input_sha256)
        self._validate_latency(self.latency_ms)
        self._validate_timestamp("started_at", self.started_at)
        self._validate_timestamp("ended_at", self.ended_at)
        if self.ended_at < self.started_at:
            raise AppValidationError("ended_at must not be before started_at")

        if self.status is ToolInvocationStatus.SUCCEEDED:
            self._validate_optional_text(
                "output_summary",
                self.output_summary,
                4096,
                required=True,
            )
            self._validate_sha256("output_sha256", self.output_sha256)
            if self.error_code is not None:
                raise AppValidationError(
                    "error_code must be empty for successful invocation"
                )
        else:
            self._validate_optional_text(
                "output_summary",
                self.output_summary,
                4096,
                required=False,
            )
            if self.output_sha256 is not None:
                self._validate_sha256("output_sha256", self.output_sha256)
            self._validate_optional_text(
                "error_code",
                self.error_code,
                128,
                required=True,
            )

    @staticmethod
    def _validate_text(field_name: str, value: str, maximum: int) -> None:
        """校验必填文本，阻止空值、超长值和首尾空白进入审计索引。"""
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {maximum}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )

    @classmethod
    def _validate_optional_text(
        cls,
        field_name: str,
        value: str | None,
        maximum: int,
        *,
        required: bool,
    ) -> None:
        """按状态校验可选文本，避免使用空字符串表达缺失值。"""
        if value is None:
            if required:
                raise AppValidationError(f"{field_name} is required")
            return
        cls._validate_text(field_name, value, maximum)

    @staticmethod
    def _validate_sha256(field_name: str, value: str | None) -> None:
        """要求摘要使用固定长度小写十六进制 SHA-256。"""
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise AppValidationError(f"{field_name} must be a SHA-256 hex digest")

    @staticmethod
    def _validate_latency(value: int) -> None:
        """延迟使用非负整数毫秒，拒绝 bool 冒充整数。"""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AppValidationError("latency_ms must be a non-negative integer")

    @staticmethod
    def _validate_timestamp(field_name: str, value: datetime) -> None:
        """要求时间携带时区，保证跨地域部署后仍可可靠排序。"""
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(f"{field_name} must include timezone information")


def build_tool_payload_sha256(payload: Mapping[str, Any]) -> str:
    """对工具输入或输出生成确定性哈希，不返回可落库的原始正文。"""
    if not isinstance(payload, Mapping):
        raise AppValidationError("tool payload must be a mapping")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AppValidationError("tool payload must be JSON serializable") from exc
    return hashlib.sha256(encoded).hexdigest()
