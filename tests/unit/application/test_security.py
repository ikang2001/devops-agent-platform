import pytest

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    PermissionDenied,
)


def test_administrator_principal_accepts_stable_identity_claims() -> None:
    """可信管理员声明应保留scope和租户集合。"""
    principal = AdministratorPrincipal(
        admin_id="admin_001",
        scopes=frozenset({"tool_permissions:write"}),
        tenant_ids=frozenset({"tenant_001"}),
    )

    principal.require_tenant_scope("tenant_001", "tool_permissions:write")


@pytest.mark.parametrize(
    "values",
    [
        {"admin_id": "admin\x7f001"},
        {"scopes": frozenset({"tool_permissions:write\x7f"})},
        {"tenant_ids": frozenset({"tenant_001\x7f"})},
        {"admin_id": "admin 001"},
    ],
)
def test_administrator_principal_rejects_dirty_claims(
    values: dict[str, object],
) -> None:
    """认证声明不能携带空白或不可见控制字符进入审计链路。"""
    defaults: dict[str, object] = {
        "admin_id": "admin_001",
        "scopes": frozenset({"tool_permissions:write"}),
        "tenant_ids": frozenset({"tenant_001"}),
    }
    defaults.update(values)

    with pytest.raises(AppValidationError):
        AdministratorPrincipal(**defaults)  # type: ignore[arg-type]


def test_require_tenant_scope_rejects_dirty_requested_identity() -> None:
    """权限检查入口也要拒绝内部调用传入的脏目标身份。"""
    principal = AdministratorPrincipal(
        admin_id="admin_001",
        scopes=frozenset({"tool_permissions:write"}),
        tenant_ids=frozenset({"tenant_001"}),
    )

    with pytest.raises(AppValidationError):
        principal.require_tenant_scope("tenant_001\x7f", "tool_permissions:write")
    with pytest.raises(PermissionDenied):
        principal.require_tenant_scope("tenant_002", "tool_permissions:write")
