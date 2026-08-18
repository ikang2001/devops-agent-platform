import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import PermissionDenied, ResourceNotFound
from devops_agent_platform.tools.mcp_adapter import (
    DemoReadonlyMCPServer,
    HttpMCPServer,
    HttpMCPServerConfig,
    MCPToolAdapter,
    MCPToolBinding,
)


@pytest.mark.asyncio
async def test_mcp_adapter_defaults_to_deny_and_redacts_sensitive_output():
    server = DemoReadonlyMCPServer({"service": {"token": "secret", "status": "up"}})
    adapter = MCPToolAdapter(
        allowlist={"demo": server},
        bindings={
            "mcp.observability.lookup@v1": MCPToolBinding(
                server_name="demo",
                tool_name="observability.lookup",
                permission_tags=("mcp:read",),
            )
        },
    )
    result = await adapter.invoke(
        "mcp.observability.lookup@v1",
        {"key": "service"},
        granted_permissions=frozenset({"mcp:read"}),
        trace_id="trace",
    )
    assert result == {"token": "[REDACTED]", "status": "up"}
    with pytest.raises(ResourceNotFound):
        await adapter.invoke(
            "unknown",
            {},
            granted_permissions=frozenset({"mcp:read"}),
            trace_id="trace",
        )


@pytest.mark.asyncio
async def test_mcp_write_binding_is_blocked():
    adapter = MCPToolAdapter(
        allowlist={"demo": DemoReadonlyMCPServer()},
        bindings={
            "write": MCPToolBinding(
                server_name="demo",
                tool_name="observability.lookup",
                permission_tags=("mcp:write",),
                risk_level=ToolRiskLevel.HIGH,
                readonly=False,
            )
        },
    )
    with pytest.raises(PermissionDenied):
        await adapter.invoke(
            "write",
            {},
            granted_permissions=frozenset({"mcp:write"}),
            trace_id="trace",
        )


@pytest.mark.asyncio
async def test_http_mcp_server_uses_bounded_jsonrpc_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "structuredContent": {
                        "found": True,
                        "token": "must-be-redacted-by-adapter",
                    },
                    "isError": False,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = HttpMCPServer(
        HttpMCPServerConfig(
            endpoint_url="https://mcp.example/mcp",
            bearer_token=SecretStr("mcp-token"),
        ),
        http_client=client,
    )
    adapter = MCPToolAdapter(
        allowlist={"approved": server},
        bindings={
            "mcp.observability.lookup@v1": MCPToolBinding(
                server_name="approved",
                tool_name="reference.observability.lookup",
                permission_tags=("mcp:read",),
            )
        },
    )
    try:
        result = await adapter.invoke(
            "mcp.observability.lookup@v1",
            {"key": "checkout-api"},
            granted_permissions=frozenset({"mcp:read"}),
            trace_id="trace-http-mcp",
        )
    finally:
        await client.aclose()

    assert result == {"found": True, "token": "[REDACTED]"}
    assert requests[0].url.path == "/mcp"
    assert requests[0].headers["authorization"] == "Bearer mcp-token"
    request_body = __import__("json").loads(requests[0].content)
    assert request_body["method"] == "tools/call"
    assert request_body["params"]["name"] == "reference.observability.lookup"


def test_http_mcp_server_rejects_plain_http_by_default() -> None:
    with pytest.raises(Exception, match="endpoint_url"):
        HttpMCPServerConfig(
            endpoint_url="http://mcp.example/mcp",
            bearer_token=SecretStr("mcp-token"),
        )
