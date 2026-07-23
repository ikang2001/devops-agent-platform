from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


def _contains_control_character(value: str) -> bool:
    """识别会污染日志、Header 或审计键的不可见 ASCII 控制字符。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


@dataclass(frozen=True)
class GetTicketDraftQuery:
    """按租户和来源工作流读取本地工单草稿。"""

    tenant_id: str
    workflow_run_id: str

    def __post_init__(self) -> None:
        """校验查询身份，禁止空值和歧义空白。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or _contains_control_character(value)
            ):
                raise AppValidationError(f"{field_name} is invalid")


@dataclass(frozen=True)
class ListTicketSubmissionsQuery:
    """按工作流读取外部工单提交状态的有限列表。"""

    tenant_id: str
    workflow_run_id: str
    limit: int = 50

    def __post_init__(self) -> None:
        """校验租户边界、来源工作流和响应容量上限。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workflow_run_id", self.workflow_run_id, 64),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or _contains_control_character(value)
            ):
                raise AppValidationError(f"{field_name} is invalid")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 100
        ):
            raise AppValidationError("limit must be between 1 and 100")
