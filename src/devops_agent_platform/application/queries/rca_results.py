from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class GetRCAExecutionResultQuery:
    """查询单次 RCA 工作流执行结果的只读应用请求。"""

    tenant_id: str
    workflow_run_id: str
    limit: int = 100

    def __post_init__(self) -> None:
        """校验租户边界、资源标识和有限读取数量。"""
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("workflow_run_id", self.workflow_run_id, 64)
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 100
        ):
            raise AppValidationError("limit must be between 1 and 100")

    @staticmethod
    def _validate_text(field_name: str, value: str, maximum: int) -> None:
        """拒绝空值、超长值和首尾空白，避免歧义查询键。"""
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {maximum}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
