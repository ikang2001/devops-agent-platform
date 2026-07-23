import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import MetricsSourceError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.observability import (
    PrometheusRangeClient,
    PrometheusRangeClientConfig,
)

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


def success_response() -> dict:
    """构造Prometheus matrix成功响应。"""
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {
                        "service": "checkout-api",
                        "instance": "10.0.0.1:9090",
                    },
                    "values": [
                        [NOW.timestamp() - 60, "1.5"],
                        [NOW.timestamp(), "2.5"],
                    ],
                }
            ],
        },
        "warnings": ["partial remote read"],
    }


async def test_query_range_sends_bounded_post_and_parses_matrix() -> None:
    """客户端应使用POST参数、租户头、Token和Trace头。"""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=success_response(), request=request)

    config = PrometheusRangeClientConfig(
        base_url="https://prometheus.example.com",
        bearer_token=SecretStr("metrics-token"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PrometheusRangeClient(config, http_client=http_client)
        result = await client.query_range(
            tenant_id="tenant_001",
            query='sum(up{service="checkout-api"})',
            start=NOW - timedelta(minutes=15),
            end=NOW,
            step_seconds=15,
            series_limit=10,
            samples_per_series_limit=100,
            trace_id="trc_001",
        )

    assert len(result.series) == 1
    assert result.series[0].labels == (
        ("instance", "10.0.0.1:9090"),
        ("service", "checkout-api"),
    )
    assert result.series[0].samples[-1][1] == "2.5"
    assert result.warnings == ("partial remote read",)
    assert result.possibly_truncated is False
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/query_range"
    assert request.headers["X-Scope-OrgID"] == "tenant_001"
    assert request.headers["X-Trace-Id"] == "trc_001"
    assert request.headers["Authorization"] == "Bearer metrics-token"
    form = parse_qs(request.content.decode())
    assert form["query"] == ['sum(up{service="checkout-api"})']
    assert form["step"] == ["15"]
    assert form["limit"] == ["10"]
    assert form["timeout"] == ["2s"]


@pytest.mark.parametrize(
    "document",
    [
        {"status": "error", "error": "query failed"},
        {
            "status": "success",
            "data": {"resultType": "vector", "result": []},
        },
        {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [{"metric": {}, "values": [["bad", "1"]]}],
            },
        },
        {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [{"metric": {}, "values": [[1, "1"], [2, "2"]]}],
            },
        },
    ],
)
async def test_invalid_prometheus_contract_is_rejected(
    document: dict,
) -> None:
    """错误envelope、类型和越界样本都不能进入Handler。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PrometheusRangeClient(
            PrometheusRangeClientConfig(base_url="https://prometheus.example.com"),
            http_client=http_client,
        )
        with pytest.raises(MetricsSourceError):
            await client.query_range(
                tenant_id="tenant_001",
                query="up",
                start=NOW - timedelta(minutes=1),
                end=NOW,
                step_seconds=15,
                series_limit=1,
                samples_per_series_limit=1,
                trace_id="trc_001",
            )


async def test_http_failure_is_sanitized_and_keeps_cause() -> None:
    """网络错误转换为稳定指标源异常，不泄漏底层地址细节。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private backend detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PrometheusRangeClient(
            PrometheusRangeClientConfig(base_url="https://prometheus.example.com"),
            http_client=http_client,
        )
        with pytest.raises(MetricsSourceError) as exc_info:
            await client.query_range(
                tenant_id="tenant_001",
                query="up",
                start=NOW - timedelta(minutes=1),
                end=NOW,
                step_seconds=15,
                series_limit=1,
                samples_per_series_limit=10,
                trace_id="trc_001",
            )

    assert "private backend" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.ReadTimeout)


async def test_oversized_response_is_stopped_during_streaming() -> None:
    """超大响应必须在流式聚合阶段终止。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 1024,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PrometheusRangeClient(
            PrometheusRangeClientConfig(
                base_url="https://prometheus.example.com",
                max_response_bytes=128,
            ),
            http_client=http_client,
        )
        with pytest.raises(MetricsSourceError, match="size"):
            await client.query_range(
                tenant_id="tenant_001",
                query="up",
                start=NOW - timedelta(minutes=1),
                end=NOW,
                step_seconds=15,
                series_limit=1,
                samples_per_series_limit=10,
                trace_id="trc_001",
            )


async def test_outer_cancellation_reaches_http_transport() -> None:
    """租约丢失时取消信号必须穿透Prometheus请求。"""
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
        client = PrometheusRangeClient(
            PrometheusRangeClientConfig(base_url="https://prometheus.example.com"),
            http_client=http_client,
        )
        task = asyncio.create_task(
            client.query_range(
                tenant_id="tenant_001",
                query="up",
                start=NOW - timedelta(minutes=1),
                end=NOW,
                step_seconds=15,
                series_limit=1,
                samples_per_series_limit=10,
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
        ("query", "up\tforged"),
        ("query", "up\x7fforged"),
        ("trace_id", "trc_001\tforged"),
        ("trace_id", "trc_001\x7fforged"),
    ],
)
async def test_dirty_query_request_is_rejected_before_network(
    field_name: str,
    value: str,
) -> None:
    """租户、查询和 Trace 边界污染必须在发起 HTTP 前失败。"""
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=success_response(), request=request)

    request = {
        "tenant_id": "tenant_001",
        "query": "up",
        "start": NOW - timedelta(minutes=1),
        "end": NOW,
        "step_seconds": 15,
        "series_limit": 1,
        "samples_per_series_limit": 10,
        "trace_id": "trc_001",
    }
    request[field_name] = value
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PrometheusRangeClient(
            PrometheusRangeClientConfig(base_url="https://prometheus.example.com"),
            http_client=http_client,
        )
        with pytest.raises(AppValidationError):
            await client.query_range(**request)

    assert called is False


def test_invalid_client_configuration_and_request_are_rejected() -> None:
    """URL、凭据类型和时间范围错误在网络调用前失败。"""
    with pytest.raises(AppValidationError):
        PrometheusRangeClientConfig(base_url="file:///metrics")
    with pytest.raises(AppValidationError):
        PrometheusRangeClientConfig(base_url="https://user:pass@prometheus.example.com")
    with pytest.raises(AppValidationError):
        PrometheusRangeClientConfig(base_url="https://prometheus.example.com\nforged")
    with pytest.raises(AppValidationError):
        PrometheusRangeClientConfig(base_url="https://prometheus.example.com\x7fforged")
    with pytest.raises(AppValidationError):
        PrometheusRangeClientConfig(
            base_url="https://prometheus.example.com",
            bearer_token="plain-text",  # type: ignore[arg-type]
        )
    with pytest.raises(AppValidationError):
        PrometheusRangeClientConfig(
            base_url="https://prometheus.example.com",
            bearer_token=SecretStr("metrics\ttoken"),
        )
    with pytest.raises(AppValidationError):
        PrometheusRangeClientConfig(
            base_url="https://prometheus.example.com",
            bearer_token=SecretStr("metrics\x7ftoken"),
        )
    with pytest.raises(AppValidationError):
        PrometheusRangeClientConfig(
            base_url="https://prometheus.example.com",
            tenant_header_name="X-Scope-OrgID\x7f",
        )
