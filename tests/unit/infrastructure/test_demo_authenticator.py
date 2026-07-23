import pytest
from pydantic import SecretStr

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    AuthenticationRequired,
)
from devops_agent_platform.infrastructure.auth import (
    DemoAdministratorAuthenticator,
    DemoAdministratorAuthenticatorConfig,
)

DEMO_TOKEN = "local-demo-administrator-token-123456"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("tenant_id", ""),
        ("tenant_id", "tenant forged"),
        ("admin_id", "\tadmin"),
        ("admin_id", "admin\x7f"),
    ],
)
def test_demo_authenticator_rejects_invalid_fixed_identity(
    field_name: str,
    value: str,
) -> None:
    values = {
        "token": SecretStr(DEMO_TOKEN),
        "tenant_id": "demo-tenant",
        "admin_id": "demo-admin",
    }
    values[field_name] = value

    with pytest.raises(AppValidationError):
        DemoAdministratorAuthenticatorConfig(**values)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "short",
        "x" * 31,
        f"{'x' * 32} ",
        f"{'x' * 32}\n",
        "令牌" * 16,
    ],
)
def test_demo_authenticator_rejects_weak_or_dirty_tokens(token: str) -> None:
    with pytest.raises(AppValidationError):
        DemoAdministratorAuthenticatorConfig(token=SecretStr(token))


async def test_demo_authenticator_returns_fixed_least_scope_principal() -> None:
    authenticator = DemoAdministratorAuthenticator(
        DemoAdministratorAuthenticatorConfig(
            token=SecretStr(DEMO_TOKEN),
            tenant_id="tenant-demo",
            admin_id="admin-demo",
        )
    )

    principal = await authenticator.authenticate(DEMO_TOKEN)

    assert principal.admin_id == "admin-demo"
    assert principal.tenant_ids == frozenset({"tenant-demo"})
    assert principal.all_tenants is False
    assert {
        "incidents:rca",
        "rca:read",
        "runbooks:write",
        "tool_permissions:write",
    }.issubset(principal.scopes)


async def test_demo_authenticator_rejects_wrong_token() -> None:
    authenticator = DemoAdministratorAuthenticator(
        DemoAdministratorAuthenticatorConfig(token=SecretStr(DEMO_TOKEN))
    )

    with pytest.raises(AuthenticationRequired, match="invalid"):
        await authenticator.authenticate("wrong-token")
