import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import RemediationGatewayError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.remediation import (
    RemediationExecutionOutcome,
    RemediationExecutionRequest,
)
from devops_agent_platform.tools.sanitization import redact_sensitive_text

_PHASE_PATHS = {
    "dry_run": "/v1/remediations/dry-run",
    "execute": "/v1/remediations/execute",
    "rollback": "/v1/remediations/rollback",
}


@dataclass(frozen=True)
class HttpRemediationExecutorConfig:
    """固定自动化控制器地址与有界 HTTP 配置。"""

    base_url: str
    bearer_token: SecretStr | None = field(default=None, repr=False)
    request_timeout_seconds: float = 10.0
    max_response_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str):
            raise AppValidationError("base_url must be a string")
        parsed = urlparse(self.base_url)
        if (
            self.base_url != self.base_url.strip()
            or self.base_url.endswith("/")
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.base_url
            )
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise AppValidationError("base_url must be an origin URL")
        if (
            isinstance(self.request_timeout_seconds, bool)
            or not isinstance(self.request_timeout_seconds, int | float)
            or not 0 < float(self.request_timeout_seconds) <= 60
        ):
            raise AppValidationError("request_timeout_seconds is invalid")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 1 <= self.max_response_bytes <= 1024 * 1024
        ):
            raise AppValidationError("max_response_bytes is invalid")
        if self.bearer_token is not None:
            if not isinstance(self.bearer_token, SecretStr):
                raise AppValidationError(
                    "bearer_token must be a SecretStr or None"
                )
            token = self.bearer_token.get_secret_value()
            if (
                not 1 <= len(token) <= 8192
                or token != token.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in token
                )
            ):
                raise AppValidationError("bearer_token is invalid")


class HttpRemediationExecutor:
    """调用只接受预注册 action_key 的外部自动化控制器。"""

    def __init__(
        self,
        config: HttpRemediationExecutorConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(config, HttpRemediationExecutorConfig):
            raise AppValidationError(
                "config must be a HttpRemediationExecutorConfig"
            )
        self._config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
        )
        self._closed = False

    async def dry_run(
        self,
        request: RemediationExecutionRequest,
    ) -> RemediationExecutionOutcome:
        return await self._invoke("dry_run", request)

    async def execute(
        self,
        request: RemediationExecutionRequest,
    ) -> RemediationExecutionOutcome:
        return await self._invoke("execute", request)

    async def rollback(
        self,
        request: RemediationExecutionRequest,
    ) -> RemediationExecutionOutcome:
        return await self._invoke("rollback", request)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def _invoke(
        self,
        phase: str,
        request: RemediationExecutionRequest,
    ) -> RemediationExecutionOutcome:
        if not isinstance(request, RemediationExecutionRequest):
            raise AppValidationError(
                "request must be a RemediationExecutionRequest"
            )
        if self._closed:
            raise RemediationGatewayError("Remediation gateway is closed")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": request.idempotency_key,
            "X-Trace-Id": request.trace_id,
        }
        if self._config.bearer_token is not None:
            headers["Authorization"] = (
                f"Bearer {self._config.bearer_token.get_secret_value()}"
            )
        content = bytearray()
        try:
            async with self._client.stream(
                "POST",
                f"{self._config.base_url}{_PHASE_PATHS[phase]}",
                headers=headers,
                json=self._body(request),
                timeout=self._config.request_timeout_seconds,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise RemediationGatewayError(
                        "Remediation controller rejected the request"
                    )
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._config.max_response_bytes:
                        raise RemediationGatewayError(
                            "Remediation response exceeds size limit"
                        )
        except asyncio.CancelledError:
            raise
        except RemediationGatewayError:
            raise
        except httpx.HTTPError:
            raise RemediationGatewayError(
                "Remediation controller request failed"
            ) from None
        return self._parse(bytes(content))

    @staticmethod
    def _body(request: RemediationExecutionRequest) -> dict[str, Any]:
        return {
            "remediation_plan_id": request.remediation_plan_id,
            "tenant_id": request.tenant_id,
            "incident_id": request.incident_id,
            "workflow_run_id": request.workflow_run_id,
            "action_key": request.action_key,
            "rollback_action_key": request.rollback_action_key,
            "target": request.target,
        }

    @staticmethod
    def _parse(content: bytes) -> RemediationExecutionOutcome:
        try:
            document = json.loads(
                content,
                parse_constant=_reject_json_constant,
            )
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
        ):
            raise RemediationGatewayError(
                "Remediation controller returned invalid JSON"
            ) from None
        if (
            not isinstance(document, dict)
            or set(document) != {"succeeded", "summary"}
            or not isinstance(document.get("succeeded"), bool)
            or not isinstance(document.get("summary"), str)
        ):
            raise RemediationGatewayError(
                "Remediation controller response contract is invalid"
            )
        summary = "\n".join(
            line.strip() for line in document["summary"].splitlines()
        ).strip()
        summary = redact_sensitive_text(summary)[0][:4096].strip()
        try:
            return RemediationExecutionOutcome(
                succeeded=document["succeeded"],
                summary=summary,
            )
        except AppValidationError as exc:
            raise RemediationGatewayError(
                "Remediation controller response outcome is invalid"
            ) from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")
