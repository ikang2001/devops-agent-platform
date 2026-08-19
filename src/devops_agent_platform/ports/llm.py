import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from devops_agent_platform.domain.enums import EvidenceType, RCAConclusionStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.rca_report import RCAReportCandidate


@dataclass(frozen=True)
class LLMReportEvidence:
    """允许发送给模型的最小 Evidence 投影。

    这里故意不包含 ``content_json``。模型分析只使用经过脱敏和限长的摘要，
    避免原始日志、令牌或用户数据被无意发送给外部模型供应商。
    """

    evidence_id: str
    evidence_type: EvidenceType
    source: str
    summary: str
    confidence: float

    def __post_init__(self) -> None:
        """校验模型输入投影，阻止无界文本和非法置信度外发。"""
        _validate_text("evidence_id", self.evidence_id, 64)
        if not isinstance(self.evidence_type, EvidenceType):
            raise AppValidationError("evidence_type must be an EvidenceType")
        _validate_text("source", self.source, 128)
        _validate_text("summary", self.summary, 4096)
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, int | float)
            or not math.isfinite(float(self.confidence))
            or not 0 <= float(self.confidence) <= 1
        ):
            raise AppValidationError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class LLMReportRequest:
    """供应商无关的结构化 RCA 模型请求。"""

    tenant_id: str
    incident_id: str
    workflow_run_id: str
    execution_attempt: int
    trace_id: str
    prompt_version: str
    evidence: tuple[LLMReportEvidence, ...]
    candidates: tuple[RCAReportCandidate, ...] = ()
    recommended_status: RCAConclusionStatus = RCAConclusionStatus.UNDETERMINED

    def __post_init__(self) -> None:
        """校验请求身份和容量，避免适配器绕过上游约束。"""
        _validate_text("tenant_id", self.tenant_id, 128)
        _validate_text("incident_id", self.incident_id, 64)
        _validate_text("workflow_run_id", self.workflow_run_id, 64)
        _validate_text("trace_id", self.trace_id, 128)
        _validate_text("prompt_version", self.prompt_version, 32)
        if (
            isinstance(self.execution_attempt, bool)
            or not isinstance(self.execution_attempt, int)
            or self.execution_attempt < 1
        ):
            raise AppValidationError("execution_attempt must be a positive integer")
        if (
            not isinstance(self.evidence, tuple)
            or not 1 <= len(self.evidence) <= 100
            or not all(isinstance(item, LLMReportEvidence) for item in self.evidence)
        ):
            raise AppValidationError(
                "evidence must contain between 1 and 100 projections"
            )
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise AppValidationError("evidence IDs must be unique")
        if (
            not isinstance(self.candidates, tuple)
            or len(self.candidates) > 5
            or not all(isinstance(item, RCAReportCandidate) for item in self.candidates)
        ):
            raise AppValidationError("candidates must contain at most five items")
        for candidate in self.candidates:
            candidate.validate()
        if not isinstance(self.recommended_status, RCAConclusionStatus):
            raise AppValidationError("recommended_status is invalid")
        if self.recommended_status is RCAConclusionStatus.CONFIRMED:
            raise AppValidationError("recommended_status cannot be confirmed")


class LLMReportGatewayPort(Protocol):
    """调用具体模型供应商并返回未经信任的结构化响应。"""

    async def generate_report(
        self,
        request: LLMReportRequest,
    ) -> Mapping[str, Any]:
        """生成 RCA 候选结论；超时策略由上层弹性适配器统一控制。"""
        ...


def _validate_text(field_name: str, value: str, maximum: int) -> None:
    """校验外发字段，拒绝空值、超长值和控制字符污染。"""
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise AppValidationError(f"{field_name} length must be between 1 and {maximum}")
    if value != value.strip():
        raise AppValidationError(
            f"{field_name} must not contain surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise AppValidationError(f"{field_name} must not contain control characters")
