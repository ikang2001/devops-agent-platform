from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


def _contains_control_character(value: str) -> bool:
    """识别权限查询键中会污染日志和审计索引的不可见字符。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


@dataclass(frozen=True)
class GetToolPermissionsQuery:
    """按租户和操作者读取当前工具权限快照。"""

    tenant_id: str
    operator_id: str

    def __post_init__(self) -> None:
        """校验查询边界，避免空白和超长身份进入数据层。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("operator_id", self.operator_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or _contains_control_character(value)
            ):
                raise AppValidationError(f"{field_name} is invalid")
