import hashlib
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.domain.enums import RCAConclusionStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.rca_report import RCAReport

Clock = Callable[[], datetime]


class DeterministicRCAReportGenerator:
    """只汇总证据、不推断根因的确定性报告生成器。"""

    GENERATOR_NAME = "deterministic-evidence-summary"
    GENERATOR_VERSION = "v1"

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    async def generate(
        self,
        command: ExecuteRCAWorkflowCommand,
        evidence: tuple[Evidence, ...],
    ) -> RCAReport:
        """生成明确标记为未确定根因的基础报告。"""
        validate_report_evidence(command, evidence)
        ordered = tuple(
            sorted(evidence, key=lambda item: item.evidence_id)
        )
        type_counts = Counter(
            item.evidence_type.value for item in ordered
        )
        evidence_type_counts = tuple(sorted(type_counts.items()))
        count_summary = ", ".join(
            f"{name}={count}"
            for name, count in evidence_type_counts
        )
        generated_at = self._now()
        return RCAReport(
            report_id=self._build_report_id(command, ordered),
            tenant_id=command.tenant_id,
            incident_id=command.incident_id,
            workflow_run_id=command.workflow_run_id,
            execution_attempt=command.execution_attempt,
            conclusion_status=RCAConclusionStatus.UNDETERMINED,
            title="Root cause requires human review",
            summary=(
                f"Collected {len(ordered)} evidence items "
                f"({count_summary}). No verified root cause candidate "
                "was produced by the deterministic generator."
            ),
            confidence=0.0,
            evidence_ids=tuple(item.evidence_id for item in ordered),
            evidence_type_counts=evidence_type_counts,
            recommendations=(
                "Review the cited evidence before taking remediation action.",
            ),
            generator_name=self.GENERATOR_NAME,
            generator_version=self.GENERATOR_VERSION,
            generated_at=generated_at,
        )

    @classmethod
    def _build_report_id(
        cls,
        command: ExecuteRCAWorkflowCommand,
        evidence: tuple[Evidence, ...],
    ) -> str:
        """根据执行身份、生成器版本和 Evidence 内容生成稳定 ID。"""
        parts = [
            command.tenant_id,
            command.workflow_run_id,
            str(command.execution_attempt),
            cls.GENERATOR_NAME,
            cls.GENERATOR_VERSION,
        ]
        parts.extend(
            f"{item.evidence_id}:{item.content_sha256}"
            for item in evidence
        )
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

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


def validate_report_evidence(
    command: ExecuteRCAWorkflowCommand,
    evidence: tuple[Evidence, ...],
) -> None:
    """拒绝空证据、重复引用、跨租户证据和不一致执行代次。"""
    if (
        not isinstance(evidence, tuple)
        or not 1 <= len(evidence) <= 100
        or not all(isinstance(item, Evidence) for item in evidence)
    ):
        raise AppValidationError(
            "report evidence must contain between 1 and 100 items"
        )
    evidence_ids = [item.evidence_id for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise AppValidationError("report evidence IDs must be unique")
    for item in evidence:
        if (
            item.tenant_id != command.tenant_id
            or item.incident_id != command.incident_id
            or item.workflow_run_id != command.workflow_run_id
            or item.execution_attempt != command.execution_attempt
        ):
            raise AppValidationError(
                "report evidence does not match workflow execution"
            )
