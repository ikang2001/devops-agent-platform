import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import TracesSourceError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.observability import (
    TempoSearchClient,
    TempoSearchClientConfig,
)

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


def success_response() -> dict:
    """构造Tempo TraceQL搜索成功响应。"""
    return {
        "traces": [
            {
                "traceID": "2f3e0cee77ae5dc9c17ade3689eb2e54",
                "rootServiceName": "checkout-api",
                "rootTraceName": "POST /checkout",
                "startTimeUnixNano": "1782734400000000000",
                "durationMs": 557.5,
                "spanSets": [
                    {
                        "matched": 2,
                        "spans": [
                            {
                                "spanID": "563d623c76514f8e",
                                "attributes": [
                                    {
                                        "key": "password",
                                        "value": {"stringValue": "must-not-copy"},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
        "metrics": {
            "inspectedTraces": 3100,
            "inspectedBytes": "3811736",
            "completedJobs": 4,
            "totalJobs": 4,
        },
    }


async def test_search_sends_bounded_get_and_parses_trace_summaries() -> None:
    """客户端应发送TraceQL参数、租户头、Token和Trace头。"""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=success_response(), request=request)

    config = TempoSearchClientConfig(
        base_url="https://tempo.example.com/",
        bearer_token=SecretStr("traces-token"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(config, http_client=http_client)
        result = await client.search(
            tenant_id="tenant_001",
            query=(
                '{ resource.service.name = "checkout-api" && '
                "span:status = error } with (most_recent=true)"
            ),
            start=NOW - timedelta(minutes=15),
            end=NOW,
            limit=10,
            spans_per_span_set=1,
            trace_id="trc_001",
        )

    assert len(result.traces) == 1
    assert result.traces[0].trace_id == ("2f3e0cee77ae5dc9c17ade3689eb2e54")
    assert result.traces[0].matched_spans == 2
    assert not hasattr(result.traces[0], "attributes")
    assert result.inspected_traces == 3100
    assert result.inspected_bytes == 3811736
    assert result.possibly_truncated is False
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/search"
    assert request.headers["X-Scope-OrgID"] == "tenant_001"
    assert request.headers["X-Trace-Id"] == "trc_001"
    assert request.headers["Authorization"] == "Bearer traces-token"
    assert request.url.params["limit"] == "10"
    assert request.url.params["spss"] == "1"
    assert request.url.params["start"] == "1782733500"
    assert request.url.params["end"] == "1782734400"
    assert "most_recent=true" in request.url.params["q"]


async def test_search_restores_trace_id_leading_zero_omitted_by_tempo() -> None:
    """Tempo may serialize a 128-bit trace ID without its leading zero."""
    document = success_response()
    document["traces"][0]["traceID"] = "54f5d963ab8b2a07fbe2a340331cbd3"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(
            TempoSearchClientConfig(base_url="https://tempo.example.com"),
            http_client=http_client,
        )
        result = await client.search(
            tenant_id="tenant_001",
            query="{}",
            start=NOW - timedelta(minutes=1),
            end=NOW,
            limit=1,
            spans_per_span_set=1,
            trace_id="trc_001",
        )

    assert result.traces[0].trace_id == "054f5d963ab8b2a07fbe2a340331cbd3"


@pytest.mark.parametrize(
    "document",
    [
        {"metrics": {}},
        {
            "traces": [
                {
                    "traceID": "invalid",
                    "rootServiceName": "api",
                    "rootTraceName": "GET /",
                    "startTimeUnixNano": "1",
                    "durationMs": 1,
                }
            ]
        },
        {
            "traces": [
                {
                    "traceID": "2f3e0cee77ae5dc9c17ade3689eb2e54",
                    "rootServiceName": "api",
                    "rootTraceName": "GET /",
                    "startTimeUnixNano": "1",
                    "durationMs": -1,
                }
            ]
        },
        {
            "traces": [
                {
                    "traceID": "2f3e0cee77ae5dc9c17ade3689eb2e54",
                    "rootServiceName": "api",
                    "rootTraceName": "GET /",
                    "startTimeUnixNano": "1",
                    "durationMs": 1,
                    "spanSets": [
                        {
                            "matched": 2,
                            "spans": [{}, {}],
                        }
                    ],
                }
            ]
        },
        {
            "traces": [],
            "metrics": {"inspectedBytes": "not-an-integer"},
        },
        {
            "traces": [],
            "metrics": {"completedJobs": 2, "totalJobs": 1},
        },
    ],
)
async def test_invalid_tempo_contract_is_rejected(document: dict) -> None:
    """结构漂移、非法摘要、越界Span和扫描指标不能进入Handler。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(
            TempoSearchClientConfig(base_url="https://tempo.example.com"),
            http_client=http_client,
        )
        with pytest.raises(TracesSourceError):
            await client.search(
                tenant_id="tenant_001",
                query="{}",
                start=NOW - timedelta(minutes=1),
                end=NOW,
                limit=1,
                spans_per_span_set=1,
                trace_id="trc_001",
            )


async def test_limit_and_partial_jobs_mark_result_truncated() -> None:
    """达到条目上限或搜索任务未完成时必须标记结果可能不完整。"""
    document = success_response()
    document["metrics"]["completedJobs"] = 3

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(
            TempoSearchClientConfig(base_url="https://tempo.example.com"),
            http_client=http_client,
        )
        result = await client.search(
            tenant_id="tenant_001",
            query="{}",
            start=NOW - timedelta(minutes=1),
            end=NOW,
            limit=1,
            spans_per_span_set=1,
            trace_id="trc_001",
        )

    assert result.possibly_truncated is True


async def test_invalid_historical_tail_keeps_valid_recent_prefix() -> None:
    """坏的陈旧摘要不能抹掉已验证的最新Trace，但必须标记结果不完整。"""
    document = success_response()
    document["traces"].append(
        {
            "traceID": "invalid-historical-trace",
            "rootServiceName": "checkout-api",
            "rootTraceName": "GET /old",
            "startTimeUnixNano": "1",
            "durationMs": 1,
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(
            TempoSearchClientConfig(base_url="https://tempo.example.com"),
            http_client=http_client,
        )
        result = await client.search(
            tenant_id="tenant_001",
            query="{} with (most_recent=true)",
            start=NOW - timedelta(minutes=1),
            end=NOW,
            limit=10,
            spans_per_span_set=1,
            trace_id="trc_001",
        )

    assert len(result.traces) == 1
    assert result.possibly_truncated is True


async def test_rootless_trace_keeps_bounded_summary() -> None:
    """缺少根Span名称的合法Trace不应拖垮整批搜索。"""
    document = success_response()
    del document["traces"][0]["rootServiceName"]
    del document["traces"][0]["rootTraceName"]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(
            TempoSearchClientConfig(base_url="https://tempo.example.com"),
            http_client=http_client,
        )
        result = await client.search(
            tenant_id="tenant_001",
            query="{}",
            start=NOW - timedelta(minutes=1),
            end=NOW,
            limit=10,
            spans_per_span_set=1,
            trace_id="trc_001",
        )

    assert result.traces[0].root_service_name == ""
    assert result.traces[0].root_trace_name == ""


async def test_just_ingested_trace_accepts_null_duration() -> None:
    """Tempo刚写入Error Trace时可能暂未投影durationMs，其他摘要仍可使用。"""
    document = success_response()
    document["traces"][0]["durationMs"] = None

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(
            TempoSearchClientConfig(base_url="https://tempo.example.com"),
            http_client=http_client,
        )
        result = await client.search(
            tenant_id="tenant_001",
            query="{} with (most_recent=true)",
            start=NOW - timedelta(minutes=1),
            end=NOW,
            limit=10,
            spans_per_span_set=1,
            trace_id="trc_001",
        )

    assert result.traces[0].duration_ms == 0.0


async def test_http_failure_is_sanitized_and_keeps_cause() -> None:
    """网络错误转换为稳定Trace源异常，不泄漏底层地址细节。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private backend detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(
            TempoSearchClientConfig(base_url="https://tempo.example.com"),
            http_client=http_client,
        )
        with pytest.raises(TracesSourceError) as exc_info:
            await client.search(
                tenant_id="tenant_001",
                query="{}",
                start=NOW - timedelta(minutes=1),
                end=NOW,
                limit=10,
                spans_per_span_set=1,
                trace_id="trc_001",
            )

    assert "private backend" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.ReadTimeout)


async def test_oversized_response_is_stopped_during_streaming() -> None:
    """超大响应必须在流式聚合阶段终止。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1024, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(
            TempoSearchClientConfig(
                base_url="https://tempo.example.com",
                max_response_bytes=128,
            ),
            http_client=http_client,
        )
        with pytest.raises(TracesSourceError, match="size"):
            await client.search(
                tenant_id="tenant_001",
                query="{}",
                start=NOW - timedelta(minutes=1),
                end=NOW,
                limit=10,
                spans_per_span_set=1,
                trace_id="trc_001",
            )


async def test_outer_cancellation_reaches_http_transport() -> None:
    """工作流取消信号必须穿透Tempo请求。"""
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
        client = TempoSearchClient(
            TempoSearchClientConfig(base_url="https://tempo.example.com"),
            http_client=http_client,
        )
        task = asyncio.create_task(
            client.search(
                tenant_id="tenant_001",
                query="{}",
                start=NOW - timedelta(minutes=1),
                end=NOW,
                limit=10,
                spans_per_span_set=1,
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
        ("query", "{}\tforged"),
        ("query", "{}\x7fforged"),
        ("trace_id", "trc_001\tforged"),
        ("trace_id", "trc_001\x7fforged"),
    ],
)
async def test_dirty_search_request_is_rejected_before_network(
    field_name: str,
    value: str,
) -> None:
    """租户、TraceQL 和 Trace 边界污染必须在发起 HTTP 前失败。"""
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=success_response(), request=request)

    request = {
        "tenant_id": "tenant_001",
        "query": "{}",
        "start": NOW - timedelta(minutes=1),
        "end": NOW,
        "limit": 10,
        "spans_per_span_set": 1,
        "trace_id": "trc_001",
    }
    request[field_name] = value
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TempoSearchClient(
            TempoSearchClientConfig(base_url="https://tempo.example.com"),
            http_client=http_client,
        )
        with pytest.raises(AppValidationError):
            await client.search(**request)

    assert called is False


def test_invalid_client_configuration_is_rejected() -> None:
    """URL、凭据类型和租户头错误应在网络调用前失败。"""
    with pytest.raises(AppValidationError):
        TempoSearchClientConfig(base_url="file:///traces")
    with pytest.raises(AppValidationError):
        TempoSearchClientConfig(base_url="https://user:pass@tempo.example.com")
    with pytest.raises(AppValidationError):
        TempoSearchClientConfig(base_url="https://tempo.example.com\nforged")
    with pytest.raises(AppValidationError):
        TempoSearchClientConfig(base_url="https://tempo.example.com\x7fforged")
    with pytest.raises(AppValidationError):
        TempoSearchClientConfig(
            base_url="https://tempo.example.com",
            bearer_token="plain-text",  # type: ignore[arg-type]
        )
    with pytest.raises(AppValidationError):
        TempoSearchClientConfig(
            base_url="https://tempo.example.com",
            bearer_token=SecretStr("traces\ttoken"),
        )
    with pytest.raises(AppValidationError):
        TempoSearchClientConfig(
            base_url="https://tempo.example.com",
            bearer_token=SecretStr("traces\x7ftoken"),
        )
    with pytest.raises(AppValidationError):
        TempoSearchClientConfig(
            base_url="https://tempo.example.com",
            tenant_header_name="bad header",
        )
    with pytest.raises(AppValidationError):
        TempoSearchClientConfig(
            base_url="https://tempo.example.com",
            tenant_header_name="X-Scope-OrgID\x7f",
        )
