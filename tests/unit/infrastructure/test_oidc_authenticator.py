import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from devops_agent_platform.application.exceptions import (
    AuthenticationServiceError,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    AuthenticationRequired,
)
from devops_agent_platform.infrastructure.auth import (
    OIDCAdministratorAuthenticator,
    OIDCAuthenticatorConfig,
)

ISSUER = "https://identity.example.com/"
AUDIENCE = "devops-agent-api"
JWKS_URL = "https://identity.example.com/.well-known/jwks.json"


@dataclass(frozen=True)
class RSAKeyMaterial:
    """测试JWT签名和JWKS响应使用的RSA密钥材料。"""

    key_id: str
    private_key: rsa.RSAPrivateKey
    jwk: dict[str, Any]


def generate_key(key_id: str) -> RSAKeyMaterial:
    """生成带kid、alg和签名用途的RSA JWK。"""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(
        private_key.public_key(),
        as_dict=True,
    )
    jwk.update({"kid": key_id, "alg": "RS256", "use": "sig"})
    return RSAKeyMaterial(key_id, private_key, jwk)


def build_config(**overrides) -> OIDCAuthenticatorConfig:
    """构造严格OIDC配置并允许覆盖缓存参数。"""
    values = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_url": JWKS_URL,
        "leeway_seconds": 0,
    }
    values.update(overrides)
    return OIDCAuthenticatorConfig(**values)


def build_token(
    key: RSAKeyMaterial,
    *,
    claims_overrides: dict[str, Any] | None = None,
    algorithm: str = "RS256",
    header_key_id: str | None = None,
) -> str:
    """生成带标准Claim和管理员授权范围的JWT。"""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "admin_001",
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
        "scope": "openid tool_permissions:write",
        "tenant_ids": ["tenant_001", "tenant_002"],
        "all_tenants": False,
    }
    claims.update(claims_overrides or {})
    return jwt.encode(
        claims,
        key.private_key,
        algorithm=algorithm,
        headers={"kid": header_key_id or key.key_id, "typ": "JWT"},
    )


class JWKSResponder:
    """按顺序返回JWKS文档并记录网络请求次数。"""

    def __init__(
        self,
        documents: list[dict[str, Any]],
    ) -> None:
        self.documents = documents
        self.calls = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        index = min(self.calls - 1, len(self.documents) - 1)
        return httpx.Response(
            200,
            json=self.documents[index],
            request=request,
        )


async def test_valid_token_maps_verified_claims_to_principal() -> None:
    """只有验签并通过标准Claim校验后才构造管理员主体。"""
    key = generate_key("key-1")
    responder = JWKSResponder([{"keys": [key.jwk]}])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )

        principal = await authenticator.authenticate(build_token(key))

    assert principal.admin_id == "admin_001"
    assert principal.scopes == frozenset(
        {"openid", "tool_permissions:write"}
    )
    assert principal.tenant_ids == frozenset(
        {"tenant_001", "tenant_002"}
    )
    assert principal.all_tenants is False
    assert responder.calls == 1


async def test_fresh_jwks_cache_avoids_repeated_network_calls() -> None:
    """同一kid在缓存有效期内不重复访问身份服务。"""
    key = generate_key("key-1")
    responder = JWKSResponder([{"keys": [key.jwk]}])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )
        token = build_token(key)

        await authenticator.authenticate(token)
        await authenticator.authenticate(token)

    assert responder.calls == 1


async def test_unknown_kid_refreshes_once_and_supports_rotation() -> None:
    """新kid触发一次刷新，随后使用轮换后的公钥验签。"""
    first_key = generate_key("key-1")
    rotated_key = generate_key("key-2")
    responder = JWKSResponder(
        [
            {"keys": [first_key.jwk]},
            {"keys": [first_key.jwk, rotated_key.jwk]},
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )

        await authenticator.authenticate(build_token(first_key))
        principal = await authenticator.authenticate(
            build_token(rotated_key)
        )

    assert principal.admin_id == "admin_001"
    assert responder.calls == 2


async def test_random_unknown_kids_are_globally_throttled() -> None:
    """连续随机kid不能逐个触发JWKS网络刷新。"""
    trusted_key = generate_key("trusted")
    attacker_one = generate_key("random-1")
    attacker_two = generate_key("random-2")
    responder = JWKSResponder([{"keys": [trusted_key.jwk]}])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(unknown_kid_cache_seconds=30),
            http_client=client,
        )
        await authenticator.authenticate(build_token(trusted_key))

        with pytest.raises(AuthenticationRequired):
            await authenticator.authenticate(build_token(attacker_one))
        with pytest.raises(AuthenticationRequired):
            await authenticator.authenticate(build_token(attacker_two))

    # 首次可信加载一次，未知kid只允许额外刷新一次。
    assert responder.calls == 2


async def test_concurrent_authentication_uses_singleflight_refresh() -> None:
    """并发首次认证共享一次JWKS刷新。"""
    key = generate_key("key-1")
    responder = JWKSResponder([{"keys": [key.jwk]}])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )
        token = build_token(key)

        principals = await asyncio.gather(
            *(authenticator.authenticate(token) for _ in range(20))
        )

    assert all(item.admin_id == "admin_001" for item in principals)
    assert responder.calls == 1


@pytest.mark.parametrize(
    "claims_overrides",
    [
        {"iss": "https://attacker.example.com/"},
        {"aud": "another-api"},
        {"exp": lambda: datetime.now(UTC) - timedelta(minutes=1)},
        {"nbf": lambda: datetime.now(UTC) + timedelta(minutes=10)},
        {"scope": ["tool_permissions:write"]},
        {"scope": "tool_permissions:write\x7f"},
        {"sub": "admin_001\x7f"},
        {"tenant_ids": "tenant_001"},
        {"tenant_ids": ["tenant_001\x7f"]},
        {"all_tenants": "true"},
    ],
)
async def test_invalid_standard_or_authorization_claims_are_rejected(
    claims_overrides: dict[str, Any],
) -> None:
    """错误标准Claim和错误自定义Claim类型统一返回认证失败。"""
    key = generate_key("key-1")
    responder = JWKSResponder([{"keys": [key.jwk]}])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )

        with pytest.raises(AuthenticationRequired):
            await authenticator.authenticate(
                build_token(
                    key,
                    claims_overrides=_resolve_claims_overrides(
                        claims_overrides
                    ),
                )
            )


def _resolve_claims_overrides(
    claims_overrides: dict[str, Any],
) -> dict[str, Any]:
    return {
        name: value() if callable(value) else value
        for name, value in claims_overrides.items()
    }


async def test_wrong_signature_and_disallowed_algorithm_are_rejected() -> None:
    """kid匹配但签名不匹配，以及对称算法Token都不得通过。"""
    trusted_key = generate_key("trusted")
    attacker_key = generate_key("attacker")
    responder = JWKSResponder([{"keys": [trusted_key.jwk]}])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )
        wrong_signature = build_token(
            attacker_key,
            header_key_id="trusted",
        )
        with pytest.raises(AuthenticationRequired):
            await authenticator.authenticate(wrong_signature)

        now = datetime.now(UTC)
        symmetric_token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "admin_001",
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            "shared-secret-with-at-least-32-bytes",
            algorithm="HS256",
            headers={"kid": "trusted"},
        )
        with pytest.raises(AuthenticationRequired):
            await authenticator.authenticate(symmetric_token)

    # HS256在读取头部后直接拒绝，不触发额外刷新。
    assert responder.calls == 1


async def test_dirty_token_or_header_kid_is_rejected_without_jwks_refresh() -> None:
    """脏 Token 文本或 kid 不能触发身份服务网络请求。"""
    key = generate_key("key-1")
    responder = JWKSResponder([{"keys": [key.jwk]}])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )

        with pytest.raises(AuthenticationRequired):
            await authenticator.authenticate(build_token(key) + "\t")
        with pytest.raises(AuthenticationRequired):
            await authenticator.authenticate(build_token(key) + "\x7f")
        with pytest.raises(AuthenticationRequired):
            await authenticator.authenticate(
                build_token(key, header_key_id="key-1\tforged")
            )
        with pytest.raises(AuthenticationRequired):
            await authenticator.authenticate(
                build_token(key, header_key_id="key-1\x7fforged")
            )

    assert responder.calls == 0


@pytest.mark.parametrize(
    "dirty_key_id",
    ["key-1\tforged", "key-1\x7fforged"],
)
async def test_dirty_jwks_kid_is_service_contract_failure(
    dirty_key_id: str,
) -> None:
    """身份服务返回污染 kid 属于 JWKS 契约错误，不能缓存为可信密钥。"""
    key = generate_key("key-1")
    dirty_jwk = dict(key.jwk)
    dirty_jwk["kid"] = dirty_key_id
    responder = JWKSResponder([{"keys": [dirty_jwk]}])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )

        with pytest.raises(AuthenticationServiceError):
            await authenticator.authenticate(build_token(key))


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda request: httpx.Response(
            503,
            request=request,
        ),
        lambda request: httpx.Response(
            200,
            content=b"not-json",
            request=request,
        ),
        lambda request: httpx.Response(
            200,
            json={"keys": []},
            request=request,
        ),
    ],
)
async def test_jwks_http_or_contract_failure_is_service_unavailable(
    response_factory,
) -> None:
    """身份服务HTTP错误和坏JWKS属于503，不伪装成Token无效。"""
    key = generate_key("key-1")

    async def handler(request: httpx.Request) -> httpx.Response:
        return response_factory(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )

        with pytest.raises(AuthenticationServiceError):
            await authenticator.authenticate(build_token(key))


async def test_oversized_jwks_is_stopped_during_streaming_read() -> None:
    """JWKS响应超过上限时中止聚合，避免大响应占满内存。"""
    key = generate_key("key-1")
    oversized = {"keys": [key.jwk], "padding": "x" * 4096}
    responder = JWKSResponder([oversized])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(max_jwks_bytes=128),
            http_client=client,
        )

        with pytest.raises(AuthenticationServiceError, match="size"):
            await authenticator.authenticate(build_token(key))


async def test_network_timeout_is_sanitized_and_keeps_cause() -> None:
    """JWKS超时返回稳定服务异常并保留底层异常链。"""
    key = generate_key("key-1")

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("identity internal detail", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )

        with pytest.raises(AuthenticationServiceError) as exc_info:
            await authenticator.authenticate(build_token(key))

    assert "internal detail" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.ReadTimeout)


async def test_outer_cancellation_reaches_jwks_request() -> None:
    """请求取消必须穿透到HTTP传输层，不能包装成503。"""
    key = generate_key("key-1")
    started = asyncio.Event()
    cancelled = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cancelled
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise
        return httpx.Response(200, json={"keys": [key.jwk]}, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        authenticator = OIDCAdministratorAuthenticator(
            build_config(),
            http_client=client,
        )
        task = asyncio.create_task(
            authenticator.authenticate(build_token(key))
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert cancelled is True


async def test_closed_authenticator_fails_without_network_access() -> None:
    """Runtime关闭后认证器必须默认拒绝且不能重新创建连接。"""
    key = generate_key("key-1")
    authenticator = OIDCAdministratorAuthenticator(build_config())
    await authenticator.close()

    with pytest.raises(AuthenticationServiceError, match="closed"):
        await authenticator.authenticate(build_token(key))


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": "http://identity.example.com/"},
        {"issuer": "https://identity.example.com/\nforged"},
        {"issuer": "https://identity.example.com/\x7fforged"},
        {"issuer": "https://identity.example.com/?debug=true"},
        {"jwks_url": "http://identity.example.com/jwks"},
        {"jwks_url": "https://identity.example.com/jwks?debug=true"},
        {"audience": "devops-agent-api\tforged"},
        {"audience": "devops-agent-api\x7fforged"},
        {"subject_claim": "sub\x00"},
        {"subject_claim": "sub\x7f"},
        {"algorithms": ("HS256",)},
        {"algorithms": ("RS256", "RS256")},
        {"jwks_cache_ttl_seconds": 0},
        {"max_jwks_keys": 0},
    ],
)
def test_unsafe_oidc_configuration_is_rejected(overrides: dict) -> None:
    """不安全端点、算法和无界配置必须在启动前失败。"""
    with pytest.raises(AppValidationError):
        build_config(**overrides)
