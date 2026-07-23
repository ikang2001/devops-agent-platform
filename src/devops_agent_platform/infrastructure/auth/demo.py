import hmac
from dataclasses import dataclass, field

from pydantic import SecretStr

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    AuthenticationRequired,
)

_MIN_TOKEN_LENGTH = 32
_MAX_TOKEN_LENGTH = 4096
_DEMO_ADMIN_SCOPES = frozenset(
    {
        "incidents:close",
        "incidents:rca",
        "incidents:read",
        "incidents:resolve",
        "rca:cancel",
        "rca:read",
        "runbooks:write",
        "ticket_drafts:approve",
        "ticket_drafts:read",
        "ticket_drafts:submit",
        "ticket_drafts:write",
        "tool_permissions:read",
        "tool_permissions:write",
    }
)


def _contains_ascii_control(value: str) -> bool:
    """识别不可见 ASCII 控制字符，包含 DEL。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


@dataclass(frozen=True)
class DemoAdministratorAuthenticatorConfig:
    """仅用于本地演练的固定管理员身份配置。"""

    token: SecretStr = field(repr=False)
    tenant_id: str = "demo-tenant"
    admin_id: str = "demo-admin"

    def __post_init__(self) -> None:
        """在认证器装配前拒绝空 Token 和不安全身份文本。"""
        if not isinstance(self.token, SecretStr):
            raise AppValidationError("demo administrator token must be a SecretStr")
        token = self.token.get_secret_value()
        if (
            not _MIN_TOKEN_LENGTH <= len(token) <= _MAX_TOKEN_LENGTH
            or not token.isascii()
            or token != token.strip()
            or _contains_ascii_control(token)
            or any(character.isspace() for character in token)
        ):
            raise AppValidationError("demo administrator token is invalid")
        self._validate_identity("tenant_id", self.tenant_id)
        self._validate_identity("admin_id", self.admin_id)

    @staticmethod
    def _validate_identity(field_name: str, value: str) -> None:
        """固定身份必须能安全进入授权、审计和日志字段。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 128
            or value != value.strip()
            or _contains_ascii_control(value)
            or any(character.isspace() for character in value)
        ):
            raise AppValidationError(f"demo administrator {field_name} is invalid")


class DemoAdministratorAuthenticator:
    """用固定 Token 生成单租户管理员主体的本地演练认证器。"""

    def __init__(self, config: DemoAdministratorAuthenticatorConfig) -> None:
        if not isinstance(config, DemoAdministratorAuthenticatorConfig):
            raise AppValidationError(
                "config must be a DemoAdministratorAuthenticatorConfig"
            )
        self._expected_token = config.token.get_secret_value().encode("ascii")
        self._principal = AdministratorPrincipal(
            admin_id=config.admin_id,
            scopes=_DEMO_ADMIN_SCOPES,
            tenant_ids=frozenset({config.tenant_id}),
            all_tenants=False,
        )

    async def authenticate(self, bearer_token: str) -> AdministratorPrincipal:
        """以常量时间比较 Token，失败时不泄露固定身份或凭据细节。"""
        candidate = (
            bearer_token.encode("utf-8")
            if isinstance(bearer_token, str)
            else b""
        )
        if not hmac.compare_digest(candidate, self._expected_token):
            raise AuthenticationRequired("Bearer token is invalid")
        return self._principal

    async def close(self) -> None:
        """保持与 Runtime 托管认证器生命周期契约一致。"""
        return None
