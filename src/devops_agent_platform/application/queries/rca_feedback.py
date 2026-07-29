from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class ListRCAFeedbackQuery:
    """按租户和工作流读取有限反馈历史。"""

    tenant_id: str
    workflow_run_id: str
    limit: int = 50

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{name} is invalid")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 100
        ):
            raise AppValidationError("limit must be between 1 and 100")


@dataclass(frozen=True)
class GetRCAFeedbackEvaluationCandidateQuery:
    """读取一条指定反馈对应的人工策展候选。"""

    tenant_id: str
    workflow_run_id: str
    feedback_id: str

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("feedback_id", self.feedback_id, 64),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(character.isspace() for character in value)
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in value
                )
            ):
                raise AppValidationError(f"{name} is invalid")
