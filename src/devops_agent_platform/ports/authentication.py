from typing import Protocol

from devops_agent_platform.application.security import AdministratorPrincipal


class AdministratorAuthenticatorPort(Protocol):
    """把不可信Bearer凭证转换为可信管理员主体的认证端口。"""

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        """认证失败时抛出AuthenticationRequired。"""
        ...


class AlertWebhookAuthenticatorPort(Protocol):
    """验证外部告警Webhook的请求级机器身份。"""

    def verify(
        self,
        *,
        timestamp: str | None,
        signature: str | None,
        body: bytes,
    ) -> None:
        """认证失败时抛出WebhookAuthenticationRequired。"""
        ...
