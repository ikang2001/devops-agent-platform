import json

import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import RemediationGatewayError
from devops_agent_platform.infrastructure.remediation import (
    HttpRemediationExecutor,
    HttpRemediationExecutorConfig,
)
from devops_agent_platform.ports.remediation import (
    RemediationExecutionRequest,
)


def build_request() -> RemediationExecutionRequest:
    return RemediationExecutionRequest(
        remediation_plan_id="rmp_001",
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        action_key="restart_inventory",
        rollback_action_key="restore_inventory_revision",
        target="inventory",
        idempotency_key="execute:rmp_001",
        trace_id="trc_execute_001",
    )


async def test_executor_uses_fixed_paths_and_bounded_contract() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"succeeded": True, "summary": "Action accepted."},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executor = HttpRemediationExecutor(
        HttpRemediationExecutorConfig(
            base_url="https://automation.example",
            bearer_token=SecretStr("controller-token"),
        ),
        http_client=client,
    )
    try:
        outcome = await executor.execute(build_request())
    finally:
        await client.aclose()

    assert outcome.succeeded is True
    assert requests[0].url.path == "/v1/remediations/execute"
    assert requests[0].headers["idempotency-key"] == "execute:rmp_001"
    assert requests[0].headers["authorization"] == "Bearer controller-token"
    body = json.loads(requests[0].content)
    assert body["action_key"] == "restart_inventory"
    assert "command" not in body
    assert "url" not in body


async def test_executor_rejects_redirects_invalid_json_and_oversized_body() -> None:
    responses = iter(
        [
            httpx.Response(302, headers={"Location": "https://evil.example"}),
            httpx.Response(200, text="not-json"),
            httpx.Response(
                200,
                content=b"x" * 128,
            ),
        ]
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: next(responses))
    )
    executor = HttpRemediationExecutor(
        HttpRemediationExecutorConfig(
            base_url="https://automation.example",
            max_response_bytes=64,
        ),
        http_client=client,
    )
    try:
        with pytest.raises(RemediationGatewayError):
            await executor.execute(build_request())
        with pytest.raises(RemediationGatewayError):
            await executor.execute(build_request())
        with pytest.raises(RemediationGatewayError, match="size limit"):
            await executor.execute(build_request())
    finally:
        await client.aclose()
