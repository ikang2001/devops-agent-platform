from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    PermissionDenied,
)

_MAX_COLLECTION_SIZE = 1024


def _contains_ascii_control(value: str) -> bool:
    """识别身份声明中的不可见ASCII控制字符，包含DEL。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


@dataclass(frozen=True)
class AdministratorPrincipal:
    """认证适配器返回的不可变管理员身份与授权范围。"""

    admin_id: str
    scopes: frozenset[str]
    tenant_ids: frozenset[str]
    all_tenants: bool = False

    def __post_init__(self) -> None:
        """校验主体、scope和租户集合，阻止脏认证声明进入接口层。"""
        self._validate_text("admin_id", self.admin_id, 128)
        self._validate_collection("scopes", self.scopes, 128)
        self._validate_collection("tenant_ids", self.tenant_ids, 128)
        if not isinstance(self.all_tenants, bool):
            raise AppValidationError("all_tenants must be a boolean")

    def require_tenant_scope(
        self,
        tenant_id: str,
        scope: str,
    ) -> None:
        """要求管理员同时拥有指定scope和目标租户访问权。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("scope", scope, 128)
        if scope not in self.scopes:
            raise PermissionDenied("Administrator permission denied")
        if not self.all_tenants and tenant_id not in self.tenant_ids:
            raise PermissionDenied("Administrator permission denied")

    @classmethod
    def _validate_collection(
        cls,
        field_name: str,
        values: frozenset[str],
        maximum_item_length: int,
    ) -> None:
        """校验不可变声明集合及容量上限。"""
        if not isinstance(values, frozenset):
            raise AppValidationError(f"{field_name} must be a frozenset")
        if len(values) > _MAX_COLLECTION_SIZE:
            raise AppValidationError(
                f"{field_name} must not exceed {_MAX_COLLECTION_SIZE} items"
            )
        for value in values:
            cls._validate_text(
                f"{field_name} item",
                value,
                maximum_item_length,
            )

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        """拒绝空值、超长值及包含空白的身份声明。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or _contains_ascii_control(value)
            or any(character.isspace() for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")
