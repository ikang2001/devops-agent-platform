from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import ToolExecutionError
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    PermissionDenied,
    ResourceNotFound,
)


class MCPServerPort(Protocol):
    async def call(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class HttpMCPServerConfig:
    """固定 MCP Streamable HTTP 端点和有界响应配置。"""

    endpoint_url: str
    bearer_token: SecretStr = field(repr=False)
    request_timeout_seconds: float = 5.0
    max_response_bytes: int = 64 * 1024
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint_url)
        allowed_schemes = {"https"}
        if self.allow_insecure_http:
            allowed_schemes.add("http")
        if (
            not isinstance(self.endpoint_url, str)
            or self.endpoint_url != self.endpoint_url.strip()
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.endpoint_url
            )
            or parsed.scheme not in allowed_schemes
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/mcp"
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise AppValidationError("MCP endpoint_url is invalid")
        if not isinstance(self.bearer_token, SecretStr):
            raise AppValidationError("MCP bearer_token must be a SecretStr")
        token = self.bearer_token.get_secret_value()
        if (
            not 1 <= len(token) <= 8192
            or token != token.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in token)
        ):
            raise AppValidationError("MCP bearer_token is invalid")
        if (
            isinstance(self.request_timeout_seconds, bool)
            or not isinstance(self.request_timeout_seconds, int | float)
            or not 0 < float(self.request_timeout_seconds) <= 60
        ):
            raise AppValidationError("MCP request_timeout_seconds is invalid")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 1 <= self.max_response_bytes <= 1024 * 1024
        ):
            raise AppValidationError("MCP max_response_bytes is invalid")


class HttpMCPServer:
    """调用已批准的 MCP JSON-RPC 2.0 Streamable HTTP Server。"""

    def __init__(
        self,
        config: HttpMCPServerConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(config, HttpMCPServerConfig):
            raise AppValidationError("config must be an HttpMCPServerConfig")
        self._config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._closed = False

    async def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self._closed:
            raise ToolExecutionError("MCP server client is closed")
        request_id = f"mcp_{uuid4().hex}"
        content = bytearray()
        try:
            async with self._client.stream(
                "POST",
                self._config.endpoint_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": (
                        f"Bearer {self._config.bearer_token.get_secret_value()}"
                    ),
                    "MCP-Protocol-Version": "2025-06-18",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": dict(arguments)},
                },
                timeout=self._config.request_timeout_seconds,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ToolExecutionError("MCP server rejected the request")
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._config.max_response_bytes:
                        raise ToolExecutionError("MCP response exceeds size limit")
        except asyncio.CancelledError:
            raise
        except ToolExecutionError:
            raise
        except httpx.HTTPError:
            raise ToolExecutionError("MCP server request failed") from None
        return self._parse_response(bytes(content), request_id)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _parse_response(content: bytes, request_id: str) -> Mapping[str, Any]:
        try:
            document = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolExecutionError("MCP server returned invalid JSON") from exc
        if (
            not isinstance(document, Mapping)
            or document.get("jsonrpc") != "2.0"
            or document.get("id") != request_id
            or "error" in document
        ):
            raise ToolExecutionError("MCP server returned an invalid response")
        result = document.get("result")
        if not isinstance(result, Mapping) or result.get("isError") is True:
            raise ToolExecutionError("MCP tool returned an error")
        structured = result.get("structuredContent")
        if not isinstance(structured, Mapping):
            raise ToolExecutionError("MCP tool did not return structured content")
        return structured


@dataclass(frozen=True)
class MCPToolBinding:
    server_name: str
    tool_name: str
    permission_tags: tuple[str, ...]
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    timeout_ms: int = 2000
    max_result_bytes: int = 32 * 1024
    readonly: bool = True


class MCPToolAdapter:
    def __init__(
        self,
        *,
        allowlist: Mapping[str, MCPServerPort],
        bindings: Mapping[str, MCPToolBinding],
        audit: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        self._allowlist = dict(allowlist)
        self._bindings = dict(bindings)
        self._audit = audit

    async def invoke(
        self,
        tool_intent: str,
        arguments: Mapping[str, Any],
        *,
        granted_permissions: frozenset[str],
        trace_id: str,
    ) -> dict[str, Any]:
        binding = self._bindings.get(tool_intent)
        if binding is None:
            raise ResourceNotFound("MCP tool is not allowlisted")
        server = self._allowlist.get(binding.server_name)
        if server is None:
            raise PermissionDenied("MCP server is not approved")
        if not binding.readonly or binding.risk_level is not ToolRiskLevel.LOW:
            raise PermissionDenied("MCP write/high-risk tool is blocked")
        if not set(binding.permission_tags).issubset(granted_permissions):
            raise PermissionDenied("MCP tool permission denied")
        if not isinstance(arguments, Mapping):
            raise AppValidationError("MCP arguments must be an object")
        payload = dict(arguments)
        try:
            async with asyncio.timeout(binding.timeout_ms / 1000):
                result = await server.call(binding.tool_name, payload)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            await self._record(tool_intent, trace_id, "TIMEOUT")
            raise TimeoutError("MCP tool timed out") from exc
        except Exception:
            await self._record(tool_intent, trace_id, "FAILED")
            raise
        if not isinstance(result, Mapping):
            raise AppValidationError("MCP result must be an object")
        encoded = json.dumps(
            result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        if len(encoded.encode()) > binding.max_result_bytes:
            raise AppValidationError("MCP result exceeds size limit")
        redacted = _redact(result)
        await self._record(tool_intent, trace_id, "SUCCEEDED")
        return redacted

    async def _record(self, tool_intent: str, trace_id: str, outcome: str) -> None:
        if self._audit is None:
            return
        event = {"tool": tool_intent, "trace_id": trace_id, "outcome": outcome}
        result = self._audit(event)
        if asyncio.iscoroutine(result):
            await result


class DemoReadonlyMCPServer:
    def __init__(self, data: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self._data = {key: dict(value) for key, value in (data or {}).items()}

    async def call(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if tool_name != "observability.lookup":
            raise ResourceNotFound("demo MCP tool not found")
        key = str(arguments.get("key", ""))
        return self._data.get(key, {"found": False, "key": key})


def _redact(value: Mapping[str, Any]) -> dict[str, Any]:
    sensitive = ("token", "secret", "password", "private_key", "authorization")
    result: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if any(word in key_text.casefold() for word in sensitive):
            result[key_text] = "[REDACTED]"
        elif isinstance(item, Mapping):
            result[key_text] = _redact(item)
        else:
            result[key_text] = item
    return result
