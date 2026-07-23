import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import LogsSourceError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.observability import (
    LokiRangeClient,
    LokiRangeClientConfig,
)

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


def success_response() -> dict:
    """构造Loki streams成功响应。"""
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {
                        "service": "checkout-api",
                        "pod": "checkout-api-7d9f",
                    },
                    "values": [
                        ["1782734400000000000", "request failed"],
                        ["1782734399000000000", "upstream timeout"],
                    ],
                }
            ],
            "stats": {},
        },
    }


async def test_query_range_sends_bounded_get_and_parses_streams() -> None:
    """客户端应发送范围参数、租户头、Token和Trace头。"""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=success_response(), request=request)

    config = LokiRangeClientConfig(
        base_url="https://loki.example.com/",
        bearer_token=SecretStr("logs-token"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = LokiRangeClient(config, http_client=http_client)
        result = await client.query_range(
            tenant_id="tenant_001",
            query='{tenant_id="tenant_001",service="checkout-api"}',
            start=NOW - timedelta(minutes=15),
            end=NOW,
            limit=10,
            trace_id="trc_001",
        )

    assert len(result.streams) == 1
    assert result.streams[0].labels == (
        ("pod", "checkout-api-7d9f"),
        ("service", "checkout-api"),
    )
    assert result.streams[0].entries[-1][1] == "upstream timeout"
    assert result.possibly_truncated is False
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/loki/api/v1/query_range"
    assert request.headers["X-Scope-OrgID"] == "tenant_001"
    assert request.headers["X-Trace-Id"] == "trc_001"
    assert request.headers["Authorization"] == "Bearer logs-token"
    assert request.url.params["direction"] == "backward"
    assert request.url.params["limit"] == "10"
    assert request.url.params["start"] == "1782733500000000000"
    assert request.url.params["end"] == "1782734400000000000"


@pytest.mark.parametrize(
    "document",
    [
        {"status": "error", "error": "query failed"},
        {
            "status": "success",
            "data": {"resultType": "matrix", "result": []},
        },
        {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [{"stream": {}, "values": [["bad", "line"]]}],
            },
        },
        {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {},
                        "values": [["1", "one"], ["2", "two"]],
                    }
                ],
            },
        },
    ],
)
async def test_invalid_loki_contract_is_rejected(document: dict) -> None:
    """错误envelope、类型、时间戳和越界条目不能进入Handler。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = LokiRangeClient(
            LokiRangeClientConfig(base_url="https://loki.example.com"),
            http_client=http_client,
        )
        with pytest.raises(LogsSourceError):
            await client.query_range(
                tenant_id="tenant_001",
                query='{service="checkout-api"}',
                start=NOW - timedelta(minutes=1),
                end=NOW,
                limit=1,
                trace_id="trc_001",
            )


async def test_http_failure_is_sanitized_and_keeps_cause() -> None:
    """网络错误转换为稳定日志源异常，不泄漏底层地址细节。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private backend detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = LokiRangeClient(
            LokiRangeClientConfig(base_url="https://loki.example.com"),
            http_client=http_client,
        )
        with pytest.raises(LogsSourceError) as exc_info:
            await client.query_range(
                tenant_id="tenant_001",
                query='{service="checkout-api"}',
                start=NOW - timedelta(minutes=1),
                end=NOW,
                limit=10,
                trace_id="trc_001",
            )

    assert "private backend" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.ReadTimeout)


async def test_oversized_response_is_stopped_during_streaming() -> None:
    """超大响应必须在流式聚合阶段终止。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1024, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = LokiRangeClient(
            LokiRangeClientConfig(
                base_url="https://loki.example.com",
                max_response_bytes=128,
            ),
            http_client=http_client,
        )
        with pytest.raises(LogsSourceError, match="size"):
            await client.query_range(
                tenant_id="tenant_001",
                query='{service="checkout-api"}',
                start=NOW - timedelta(minutes=1),
                end=NOW,
                limit=10,
                trace_id="trc_001",
            )


async def test_outer_cancellation_reaches_http_transport() -> None:
    """工作流取消信号必须穿透Loki请求。"""
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
        return httpx.Response(200, json=success_response(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = LokiRangeClient(
            LokiRangeClientConfig(base_url="https://loki.example.com"),
            http_client=http_client,
        )
        task = asyncio.create_task(
            client.query_range(
                tenant_id="tenant_001",
                query='{service="checkout-api"}',
                start=NOW - timedelta(minutes=1),
                end=NOW,
                limit=10,
                trace_id="trc_001",
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert cancelled is True


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("tenant_id", "tenant_001\tforged"),
        ("tenant_id", "tenant_001\x7fforged"),
        ("query", '{service="checkout-api"}\tforged'),
        ("query", '{service="checkout-api"}\x7fforged'),
        ("trace_id", "trc_001\tforged"),
        ("trace_id", "trc_001\x7fforged"),
    ],
)
async def test_dirty_query_request_is_rejected_before_network(
    field_name: str,
    value: str,
) -> None:
    """租户、LogQL 和 Trace 边界污染必须在发起 HTTP 前失败。"""
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=success_response(), request=request)

    request = {
        "tenant_id": "tenant_001",
        "query": '{service="checkout-api"}',
        "start": NOW - timedelta(minutes=1),
        "end": NOW,
        "limit": 10,
        "trace_id": "trc_001",
    }
    request[field_name] = value
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = LokiRangeClient(
            LokiRangeClientConfig(base_url="https://loki.example.com"),
            http_client=http_client,
        )
        with pytest.raises(AppValidationError):
            await client.query_range(**request)

    assert called is False


def test_invalid_client_configuration_is_rejected() -> None:
    """URL、凭据类型和租户头错误应在网络调用前失败。"""
    with pytest.raises(AppValidationError):
        LokiRangeClientConfig(base_url="file:///logs")
    with pytest.raises(AppValidationError):
        LokiRangeClientConfig(base_url="https://user:pass@loki.example.com")
    with pytest.raises(AppValidationError):
        LokiRangeClientConfig(base_url="https://loki.example.com\nforged")
    with pytest.raises(AppValidationError):
        LokiRangeClientConfig(base_url="https://loki.example.com\x7fforged")
    with pytest.raises(AppValidationError):
        LokiRangeClientConfig(
            base_url="https://loki.example.com",
            bearer_token="plain-text",  # type: ignore[arg-type]
        )
    with pytest.raises(AppValidationError):
        LokiRangeClientConfig(
            base_url="https://loki.example.com",
            bearer_token=SecretStr("logs\ttoken"),
        )
    with pytest.raises(AppValidationError):
        LokiRangeClientConfig(
            base_url="https://loki.example.com",
            bearer_token=SecretStr("logs\x7ftoken"),
        )
    with pytest.raises(AppValidationError):
        LokiRangeClientConfig(
            base_url="https://loki.example.com",
            tenant_header_name="bad header",
        )
    with pytest.raises(AppValidationError):
        LokiRangeClientConfig(
            base_url="https://loki.example.com",
            tenant_header_name="X-Scope-OrgID\x7f",
        )
