from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import RCAConclusionStatus
from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class RCAReport:
    """绑定到单个工作流执行代次的不可变 RCA 结论快照。

    报告只保存有限结论、建议和 Evidence 引用，不复制 Evidence 原始内容。
    ``generator_name`` 与 ``generator_version`` 用于后续模型、Prompt 和规则升级
    时重放并比较不同版本结果。
    """

    report_id: str
    tenant_id: str
    incident_id: str
    workflow_run_id: str
    execution_attempt: int
    conclusion_status: RCAConclusionStatus
    title: str
    summary: str
    confidence: float
    evidence_ids: tuple[str, ...]
    evidence_type_counts: tuple[tuple[str, int], ...]
    recommendations: tuple[str, ...]
    generator_name: str
    generator_version: str
    generated_at: datetime

    def __post_init__(self) -> None:
        """构造时执行完整领域约束。"""
        self.validate()

    def validate(self) -> None:
        """复验报告身份、展示文本、证据引用和生成器版本。"""
        fields = (
            ("report_id", self.report_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("incident_id", self.incident_id, 64),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("title", self.title, 256),
            ("generator_name", self.generator_name, 128),
            ("generator_version", self.generator_version, 64),
        )
        for field_name, value, maximum in fields:
            self._validate_text(field_name, value, maximum)
        self._validate_text(
            "summary",
            self.summary,
            4096,
            multiline=True,
        )
        if (
            isinstance(self.execution_attempt, bool)
            or not isinstance(self.execution_attempt, int)
            or self.execution_attempt < 1
        ):
            raise AppValidationError(
                "execution_attempt must be a positive integer"
            )
        if not isinstance(self.conclusion_status, RCAConclusionStatus):
            raise AppValidationError(
                "conclusion_status must be an RCAConclusionStatus"
            )
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, int | float)
            or not 0 <= float(self.confidence) <= 1
        ):
            raise AppValidationError("confidence must be between 0 and 1")
        self._validate_evidence_ids()
        self._validate_type_counts()
        self._validate_recommendations()
        if (
            not isinstance(self.generated_at, datetime)
            or self.generated_at.tzinfo is None
            or self.generated_at.utcoffset() is None
        ):
            raise AppValidationError(
                "generated_at must include timezone information"
            )

    def _validate_evidence_ids(self) -> None:
        """报告必须引用有限且唯一的 Evidence。"""
        if (
            not isinstance(self.evidence_ids, tuple)
            or not 1 <= len(self.evidence_ids) <= 100
        ):
            raise AppValidationError(
                "evidence_ids must contain between 1 and 100 items"
            )
        for evidence_id in self.evidence_ids:
            self._validate_text("evidence_id", evidence_id, 64)
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise AppValidationError("evidence_ids must be unique")

    def _validate_type_counts(self) -> None:
        """证据类型计数必须有序、唯一且总数与引用数量一致。"""
        if (
            not isinstance(self.evidence_type_counts, tuple)
            or not self.evidence_type_counts
        ):
            raise AppValidationError(
                "evidence_type_counts must be a non-empty tuple"
            )
        names: list[str] = []
        total = 0
        for item in self.evidence_type_counts:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[1], int)
                or isinstance(item[1], bool)
                or item[1] < 1
            ):
                raise AppValidationError(
                    "evidence_type_counts item is invalid"
                )
            self._validate_text("evidence_type", item[0], 32)
            names.append(item[0])
            total += item[1]
        if names != sorted(names) or len(names) != len(set(names)):
            raise AppValidationError(
                "evidence_type_counts must be sorted and unique"
            )
        if total != len(self.evidence_ids):
            raise AppValidationError(
                "evidence_type_counts total does not match evidence_ids"
            )

    def _validate_recommendations(self) -> None:
        """限制建议数量和长度，避免报告成为无界文本容器。"""
        if (
            not isinstance(self.recommendations, tuple)
            or len(self.recommendations) > 20
        ):
            raise AppValidationError(
                "recommendations must be a tuple with at most 20 items"
            )
        for recommendation in self.recommendations:
            self._validate_text("recommendation", recommendation, 1024)

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
        *,
        multiline: bool = False,
    ) -> None:
        """拒绝空值、超长、首尾空白和不可见 ASCII 字符。"""
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {maximum}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(
            (
                ord(character) < 32
                and not (multiline and character == "\n")
            )
            or ord(character) == 127
            for character in value
        ):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )
