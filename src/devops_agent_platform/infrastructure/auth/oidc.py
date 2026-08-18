import asyncio
import json
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from jwt import PyJWK
from jwt.exceptions import InvalidTokenError

from devops_agent_platform.application.exceptions import (
    AuthenticationServiceError,
)
from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    AuthenticationRequired,
)

MonotonicClock = Callable[[], float]
_SAFE_ASYMMETRIC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
)


def _contains_ascii_control(value: str) -> bool:
    """识别不可见ASCII控制字符，包含历史上容易漏掉的DEL。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


@dataclass(frozen=True)
class OIDCAuthenticatorConfig:
    """OIDC管理员Token验证和JWKS缓存配置。"""

    issuer: str
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...] = ("RS256",)
    jwks_cache_ttl_seconds: float = 300.0
    unknown_kid_cache_seconds: float = 30.0
    request_timeout_seconds: float = 3.0
    leeway_seconds: float = 30.0
    max_token_bytes: int = 16 * 1024
    max_jwks_bytes: int = 256 * 1024
    max_jwks_keys: int = 100
    subject_claim: str = "sub"
    scopes_claim: str = "scope"
    tenants_claim: str = "tenant_ids"
    all_tenants_claim: str = "all_tenants"
    ca_bundle_path: str | None = None

    def __post_init__(self) -> None:
        """拒绝不安全算法、非HTTPS端点和无界容量配置。"""
        self._validate_https_url("issuer", self.issuer)
        self._validate_https_url("jwks_url", self.jwks_url)
        self._validate_text("audience", self.audience, 512)
        if (
            not isinstance(self.algorithms, tuple)
            or not self.algorithms
            or len(set(self.algorithms)) != len(self.algorithms)
            or not set(self.algorithms).issubset(_SAFE_ASYMMETRIC_ALGORITHMS)
        ):
            raise AppValidationError(
                "algorithms must contain unique supported asymmetric algorithms"
            )
        self._validate_positive_number(
            "jwks_cache_ttl_seconds",
            self.jwks_cache_ttl_seconds,
            86_400,
        )
        self._validate_positive_number(
            "request_timeout_seconds",
            self.request_timeout_seconds,
            30,
        )
        self._validate_positive_number(
            "unknown_kid_cache_seconds",
            self.unknown_kid_cache_seconds,
            300,
        )
        if (
            isinstance(self.leeway_seconds, bool)
            or not isinstance(self.leeway_seconds, int | float)
            or not 0 <= self.leeway_seconds <= 300
        ):
            raise AppValidationError("leeway_seconds must be between 0 and 300")
        self._validate_positive_int(
            "max_token_bytes",
            self.max_token_bytes,
            1024 * 1024,
        )
        self._validate_positive_int(
            "max_jwks_bytes",
            self.max_jwks_bytes,
            4 * 1024 * 1024,
        )
        self._validate_positive_int(
            "max_jwks_keys",
            self.max_jwks_keys,
            1000,
        )
        for field_name in (
            "subject_claim",
            "scopes_claim",
            "tenants_claim",
            "all_tenants_claim",
        ):
            self._validate_text(field_name, getattr(self, field_name), 128)
        if self.ca_bundle_path is not None:
            if (
                not isinstance(self.ca_bundle_path, str)
                or not self.ca_bundle_path
                or self.ca_bundle_path != self.ca_bundle_path.strip()
                or _contains_ascii_control(self.ca_bundle_path)
            ):
                raise AppValidationError("ca_bundle_path is invalid")

    @staticmethod
    def _validate_https_url(field_name: str, value: str) -> None:
        """仅允许配置明确HTTPS端点，阻止明文密钥获取。"""
        if (
            not isinstance(value, str)
            or value != value.strip()
            or _contains_ascii_control(value)
        ):
            raise AppValidationError(f"{field_name} must be an HTTPS URL")
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise AppValidationError(f"{field_name} must be an HTTPS URL")

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        """校验OIDC标识和Claim名称。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or _contains_ascii_control(value)
            or any(character.isspace() for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")

    @staticmethod
    def _validate_positive_number(
        field_name: str,
        value: float,
        maximum: float,
    ) -> None:
        """校验正数时间配置。"""
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not 0 < value <= maximum
        ):
            raise AppValidationError(f"{field_name} must be between 0 and {maximum}")

    @staticmethod
    def _validate_positive_int(
        field_name: str,
        value: int,
        maximum: int,
    ) -> None:
        """校验正整数容量配置。"""
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise AppValidationError(f"{field_name} must be between 1 and {maximum}")


class OIDCAdministratorAuthenticator:
    """使用异步JWKS缓存和PyJWT验证管理员Bearer Token。"""

    def __init__(
        self,
        config: OIDCAuthenticatorConfig,
        http_client: httpx.AsyncClient | None = None,
        monotonic_clock: MonotonicClock = monotonic,
    ) -> None:
        """创建认证器；传入Client时其生命周期仍归调用方所有。"""
        if not isinstance(config, OIDCAuthenticatorConfig):
            raise AppValidationError("config must be an OIDCAuthenticatorConfig")
        if not callable(monotonic_clock):
            raise AppValidationError("monotonic_clock must be callable")
        self._config = config
        self._owns_http_client = http_client is None
        verify: ssl.SSLContext | bool = True
        if config.ca_bundle_path is not None:
            try:
                verify = ssl.create_default_context(cafile=config.ca_bundle_path)
            except (OSError, ssl.SSLError) as exc:
                raise AppValidationError("OIDC CA bundle could not be loaded") from exc
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout_seconds),
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
            trust_env=False,
            verify=verify,
        )
        self._monotonic_clock = monotonic_clock
        self._keys: dict[str, PyJWK] = {}
        self._missing_kids: dict[str, float] = {}
        self._unknown_refresh_blocked_until = float("-inf")
        self._fetched_at = float("-inf")
        self._refresh_lock = asyncio.Lock()
        self._closed = False

    async def authenticate(
        self,
        bearer_token: str,
    ) -> AdministratorPrincipal:
        """验证JWT并把可信Claim映射为管理员主体。"""
        if self._closed:
            raise AuthenticationServiceError("OIDC authenticator is closed")
        self._validate_token_size(bearer_token)
        header = self._read_unverified_header(bearer_token)
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in self._config.algorithms:
            raise AuthenticationRequired("Bearer token is invalid")
        if (
            not isinstance(key_id, str)
            or not 1 <= len(key_id) <= 256
            or key_id != key_id.strip()
            or _contains_ascii_control(key_id)
        ):
            raise AuthenticationRequired("Bearer token is invalid")

        signing_key = await self._get_signing_key(
            key_id,
            algorithm,
        )
        try:
            claims = jwt.decode(
                bearer_token,
                signing_key.key,
                algorithms=list(self._config.algorithms),
                audience=self._config.audience,
                issuer=self._config.issuer,
                leeway=self._config.leeway_seconds,
                options={
                    "require": list(
                        {
                            "exp",
                            "iat",
                            "iss",
                            "sub",
                            "aud",
                            self._config.subject_claim,
                        }
                    ),
                },
            )
        except InvalidTokenError as exc:
            raise AuthenticationRequired("Bearer token is invalid") from exc
        return self._build_principal(claims)

    async def close(self) -> None:
        """释放内部创建的HTTP连接池；重复调用安全。"""
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    async def _get_signing_key(
        self,
        key_id: str,
        algorithm: str,
    ) -> PyJWK:
        """读取缓存密钥；过期或kid缺失时执行单飞刷新。"""
        key = self._cached_key(key_id, algorithm)
        if key is not None:
            return key
        if self._missing_key_is_cached(key_id):
            raise AuthenticationRequired("Bearer token signing key is unknown")

        async with self._refresh_lock:
            key = self._cached_key(key_id, algorithm)
            if key is not None:
                return key
            if self._missing_key_is_cached(key_id):
                raise AuthenticationRequired("Bearer token signing key is unknown")
            await self._refresh_keys()
            key = self._keys.get(key_id)
            if key is None or key.algorithm_name != algorithm:
                blocked_until = (
                    self._monotonic_clock() + self._config.unknown_kid_cache_seconds
                )
                self._missing_kids[key_id] = blocked_until
                self._unknown_refresh_blocked_until = blocked_until
                raise AuthenticationRequired("Bearer token signing key is unknown")
            self._missing_kids.pop(key_id, None)
            return key

    def _cached_key(
        self,
        key_id: str,
        algorithm: str,
    ) -> PyJWK | None:
        """仅在缓存新鲜且算法匹配时返回密钥。"""
        if (
            self._monotonic_clock() - self._fetched_at
            >= self._config.jwks_cache_ttl_seconds
        ):
            return None
        key = self._keys.get(key_id)
        if key is None or key.algorithm_name != algorithm:
            return None
        return key

    def _missing_key_is_cached(self, key_id: str) -> bool:
        """短期缓存未知kid，阻止随机kid触发无界JWKS刷新。"""
        if not self._cache_is_fresh():
            return False
        if self._monotonic_clock() < self._unknown_refresh_blocked_until:
            return True
        expires_at = self._missing_kids.get(key_id)
        if expires_at is None:
            return False
        if self._monotonic_clock() >= expires_at:
            self._missing_kids.pop(key_id, None)
            return False
        return True

    def _cache_is_fresh(self) -> bool:
        """判断当前JWKS快照是否仍在配置缓存窗口内。"""
        return (
            self._monotonic_clock() - self._fetched_at
            < self._config.jwks_cache_ttl_seconds
        )

    async def _refresh_keys(self) -> None:
        """有界拉取并严格解析JWKS，不复用失败响应。"""
        if self._closed:
            raise AuthenticationServiceError("OIDC authenticator is closed")
        content = bytearray()
        try:
            async with self._http_client.stream(
                "GET",
                self._config.jwks_url,
                headers={"Accept": "application/json"},
                timeout=self._config.request_timeout_seconds,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._config.max_jwks_bytes:
                        raise AuthenticationServiceError(
                            "OIDC signing key response has an invalid size"
                        )
        except asyncio.CancelledError:
            raise
        except AuthenticationServiceError:
            raise
        except httpx.HTTPError as exc:
            raise AuthenticationServiceError(
                "OIDC signing keys are unavailable"
            ) from exc

        if not content:
            raise AuthenticationServiceError(
                "OIDC signing key response has an invalid size"
            )
        try:
            document = json.loads(content)
            keys = document["keys"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise AuthenticationServiceError(
                "OIDC signing key response is invalid"
            ) from exc
        if (
            not isinstance(keys, list)
            or not keys
            or len(keys) > self._config.max_jwks_keys
        ):
            raise AuthenticationServiceError("OIDC signing key set is invalid")

        parsed_keys: dict[str, PyJWK] = {}
        try:
            for key_data in keys:
                self._add_jwk(parsed_keys, key_data)
        except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise AuthenticationServiceError("OIDC signing key set is invalid") from exc
        if not parsed_keys:
            raise AuthenticationServiceError(
                "OIDC signing key set has no verification keys"
            )
        self._keys = parsed_keys
        self._fetched_at = self._monotonic_clock()
        self._missing_kids = {
            key_id: expires_at
            for key_id, expires_at in self._missing_kids.items()
            if expires_at > self._fetched_at
        }

    def _add_jwk(
        self,
        parsed_keys: dict[str, PyJWK],
        key_data: object,
    ) -> None:
        """筛选签名用途密钥，并拒绝重复kid和算法漂移。"""
        if not isinstance(key_data, Mapping):
            raise TypeError("JWK must be an object")
        if key_data.get("use") not in (None, "sig"):
            return
        key_ops = key_data.get("key_ops")
        if key_ops is not None and (
            not isinstance(key_ops, list) or "verify" not in key_ops
        ):
            return
        key_id = key_data["kid"]
        algorithm = key_data.get("alg")
        if (
            not isinstance(key_id, str)
            or not 1 <= len(key_id) <= 256
            or key_id != key_id.strip()
            or _contains_ascii_control(key_id)
            or key_id in parsed_keys
            or algorithm not in self._config.algorithms
        ):
            raise ValueError("JWK identity or algorithm is invalid")
        parsed_keys[key_id] = PyJWK.from_dict(dict(key_data))

    def _build_principal(
        self,
        claims: Mapping[str, Any],
    ) -> AdministratorPrincipal:
        """从已验签Claim构造有限管理员授权范围。"""
        admin_id = claims.get(self._config.subject_claim)
        scopes_value = claims.get(self._config.scopes_claim, "")
        tenants_value = claims.get(self._config.tenants_claim, [])
        all_tenants = claims.get(self._config.all_tenants_claim, False)
        if not isinstance(scopes_value, str):
            raise AuthenticationRequired("Bearer token claims are invalid")
        if (
            not isinstance(tenants_value, list)
            or not all(isinstance(item, str) for item in tenants_value)
            or not isinstance(all_tenants, bool)
        ):
            raise AuthenticationRequired("Bearer token claims are invalid")
        scopes = frozenset(item for item in scopes_value.split(" ") if item)
        try:
            return AdministratorPrincipal(
                admin_id=admin_id,
                scopes=scopes,
                tenant_ids=frozenset(tenants_value),
                all_tenants=all_tenants,
            )
        except AppValidationError as exc:
            raise AuthenticationRequired("Bearer token claims are invalid") from exc

    def _validate_token_size(self, token: str) -> None:
        """在JWT解析前限制类型、空白和编码后字节数。"""
        if (
            not isinstance(token, str)
            or not token
            or token != token.strip()
            or _contains_ascii_control(token)
            or len(token.encode()) > self._config.max_token_bytes
        ):
            raise AuthenticationRequired("Bearer token is invalid")

    @staticmethod
    def _read_unverified_header(token: str) -> dict[str, Any]:
        """只读取路由密钥所需头部，不把未验签内容当作身份。"""
        try:
            return jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise AuthenticationRequired("Bearer token is invalid") from exc
