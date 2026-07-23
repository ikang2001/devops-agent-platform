import asyncio
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import TicketingGatewayError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.ticketing import (
    HttpJsonTicketingGateway,
    HttpJsonTicketingGatewayConfig,
)
from devops_agent_platform.ports.ticketing import (
    TicketingGatewaySubmitOutcome,
    TicketingSubmitOutcome,
    TicketingSubmitRequest,
)


class RecordingObserver:
    """记录 HTTP JSON 工单网关观察结果的测试替身。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[tuple[TicketingGatewaySubmitOutcome, float]] = []
        self._fail = fail

    def observe_ticketing_gateway_submit(
        self,
        outcome: TicketingGatewaySubmitOutcome,
        duration_seconds: float,
    ) -> None:
        """记录固定结果；可注入异常验证非关键路径隔离。"""
        if self._fail:
            raise RuntimeError("metrics unavailable")
        self.events.append((outcome, duration_seconds))


class FakeMonotonicClock:
    """提供可控单调时钟，避免测试依赖真实时间。"""

    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def build_request() -> TicketingSubmitRequest:
    """构造外部工单提交适配器测试使用的稳定请求。"""
    return TicketingSubmitRequest(
        tenant_id="tenant_001",
        ticket_submission_id="tsb_001",
        ticket_draft_id="tdf_001",
        target_system="jira",
        title="Database timeout candidate",
        description="RCA report suggests connection pool saturation.",
        priority="P1",
        evidence_ids=("evd_001", "evd_002"),
        recommendations=(
            "Check database connection pool metrics.",
            "Review recent deployment changes.",
        ),
        idempotency_key="ticket-submit-tsb_001-v1",
        trace_id="trc_ticket_submit_001",
    )


def build_config(
    **changes: Any,
) -> HttpJsonTicketingGatewayConfig:
    """构造不暴露令牌的 HTTP JSON 网关配置。"""
    values = {
        "endpoint_url": "https://ticketing.example.com/api/tickets",
        "bearer_token": SecretStr("private-ticketing-token"),
    }
    values.update(changes)
    return HttpJsonTicketingGatewayConfig(**values)


def test_ticketing_submit_request_allows_only_description_multiline() -> None:
    """工单正文可多行，但路由、标题和推荐项必须保持单行。"""
    request = replace(
        build_request(),
        description=(
            "RCA report suggests connection pool saturation.\n"
            "Second line is preserved for the ticket body."
        ),
    )

    assert "Second line" in request.description


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("tenant_id", "tenant_001\nforged"),
        ("ticket_submission_id", "tsb_001\rforged"),
        ("ticket_draft_id", "tdf_001\tforged"),
        ("target_system", "jira\nforged"),
        ("title", "Database timeout\ncandidate"),
        ("priority", "P1\rforged"),
        ("idempotency_key", "ticket-submit-tsb_001-v1\nforged"),
        ("trace_id", "trc_ticket_submit_001\x7fforged"),
        ("trace_id", "trc_ticket_submit_001\tforged"),
    ],
)
def test_ticketing_submit_request_rejects_dirty_single_line_fields(
    field_name: str,
    value: str,
) -> None:
    """会进入 HTTP 头、路由或供应商索引的字段不能夹带控制字符。"""
    with pytest.raises(AppValidationError):
        replace(build_request(), **{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("evidence_ids", ("evd_001\nforged",)),
        ("recommendations", ("Check database metrics.\x7fforged",)),
        ("recommendations", ("Check database metrics.\tforged",)),
        ("description", "Line one\x7fLine two"),
        ("description", "Line one\tLine two"),
    ],
)
def test_ticketing_submit_request_rejects_dirty_collections_or_description(
    field_name: str,
    value: object,
) -> None:
    """描述只允许换行，不允许制表等其它控制字符污染 JSON 载荷。"""
    with pytest.raises(AppValidationError):
        replace(build_request(), **{field_name: value})


@pytest.mark.parametrize(
    "outcome",
    [
        {
            "succeeded": True,
            "external_ticket_id": "JIRA-1001\x7fforged",
        },
        {
            "succeeded": True,
            "external_ticket_id": "JIRA-1001",
            "external_ticket_url": "https://jira.example/browse/JIRA-1001\x7f",
        },
        {
            "succeeded": False,
            "failure_reason": "target project\x7fis read-only",
        },
    ],
)
def test_ticketing_submit_outcome_rejects_del_control_character(
    outcome: dict[str, object],
) -> None:
    """供应商返回字段同样不能把DEL带入本地提交状态。"""
    with pytest.raises(AppValidationError):
        TicketingSubmitOutcome(**outcome)  # type: ignore[arg-type]


async def test_gateway_sends_minimal_ticketing_request() -> None:
    """网关应发送最小业务载荷、幂等键和 trace_id。"""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "succeeded": True,
                "external_ticket_id": "JIRA-1001",
                "external_ticket_url": "https://jira.example.com/browse/JIRA-1001",
            },
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
        )
        outcome = await gateway.submit_ticket(build_request())

    assert outcome.succeeded is True
    assert outcome.external_ticket_id == "JIRA-1001"
    assert outcome.external_ticket_url == ("https://jira.example.com/browse/JIRA-1001")
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/tickets"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Authorization"] == ("Bearer private-ticketing-token")
    assert request.headers["Idempotency-Key"] == "ticket-submit-tsb_001-v1"
    assert request.headers["X-Trace-Id"] == "trc_ticket_submit_001"

    body = json.loads(request.content)
    assert body == {
        "tenant_id": "tenant_001",
        "ticket_submission_id": "tsb_001",
        "ticket_draft_id": "tdf_001",
        "target_system": "jira",
        "title": "Database timeout candidate",
        "description": "RCA report suggests connection pool saturation.",
        "priority": "P1",
        "evidence_ids": ["evd_001", "evd_002"],
        "recommendations": [
            "Check database connection pool metrics.",
            "Review recent deployment changes.",
        ],
    }


async def test_gateway_observes_success_and_business_failure() -> None:
    """成功与业务失败应记录为不同固定结果和耗时。"""
    documents = [
        {
            "succeeded": True,
            "external_ticket_id": "JIRA-1001",
        },
        {
            "succeeded": False,
            "failure_reason": "target project is read-only",
        },
    ]
    observer = RecordingObserver()
    clock = FakeMonotonicClock()

    async def handler(request: httpx.Request) -> httpx.Response:
        clock.advance(0.25)
        return httpx.Response(
            200,
            json=documents.pop(0),
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
            observer=observer,
            monotonic_clock=clock,
        )
        first = await gateway.submit_ticket(build_request())
        second = await gateway.submit_ticket(build_request())

    assert first.succeeded is True
    assert second.succeeded is False
    assert observer.events == [
        (TicketingGatewaySubmitOutcome.SUCCESS, 0.25),
        (TicketingGatewaySubmitOutcome.BUSINESS_FAILURE, 0.25),
    ]


async def test_gateway_observes_gateway_error_without_leaking_secrets() -> None:
    """HTTP失败应记录网关错误，异常仍保持脱敏。"""
    observer = RecordingObserver()
    clock = FakeMonotonicClock()

    async def handler(request: httpx.Request) -> httpx.Response:
        clock.advance(0.5)
        return httpx.Response(
            503,
            json={"error": "private-ticketing-token"},
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
            observer=observer,
            monotonic_clock=clock,
        )
        with pytest.raises(TicketingGatewayError) as exc_info:
            await gateway.submit_ticket(build_request())

    assert observer.events == [
        (TicketingGatewaySubmitOutcome.GATEWAY_ERROR, 0.5),
    ]
    assert "private-ticketing-token" not in str(exc_info.value)


async def test_gateway_observes_cancellation() -> None:
    """外部取消应单独记录，不能混入网关错误。"""
    observer = RecordingObserver()
    clock = FakeMonotonicClock()
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            clock.advance(0.75)
        return httpx.Response(
            200,
            json={"succeeded": False, "failure_reason": "unused"},
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
            observer=observer,
            monotonic_clock=clock,
        )
        task = asyncio.create_task(gateway.submit_ticket(build_request()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert observer.events == [
        (TicketingGatewaySubmitOutcome.CANCELLED, 0.75),
    ]


async def test_observer_failure_does_not_change_gateway_result() -> None:
    """指标采集异常不能改变外部工单提交结果。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"succeeded": True, "external_ticket_id": "JIRA-1001"},
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
            observer=RecordingObserver(fail=True),
        )
        outcome = await gateway.submit_ticket(build_request())

    assert outcome.succeeded is True


async def test_gateway_returns_business_failure_outcome() -> None:
    """外部系统明确拒绝提交时应返回失败结果，而不是触发重试。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "succeeded": False,
                "failure_reason": "target project is read-only",
            },
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
        )
        outcome = await gateway.submit_ticket(build_request())

    assert outcome.succeeded is False
    assert outcome.failure_reason == "target project is read-only"
    assert outcome.external_ticket_id is None


async def test_gateway_redacts_business_failure_reason() -> None:
    """供应商业务失败原因进入应用层前必须脱敏和单行化。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "succeeded": False,
                "failure_reason": (
                    "provider password=hunter2 token=secret-token\ninternal\ttrace"
                ),
            },
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
        )
        outcome = await gateway.submit_ticket(build_request())

    assert outcome.succeeded is False
    assert outcome.failure_reason == (
        "provider password=[REDACTED] token=[REDACTED] internal trace"
    )
    assert "hunter2" not in str(outcome.failure_reason)
    assert "secret-token" not in str(outcome.failure_reason)


async def test_gateway_removes_del_from_business_failure_reason() -> None:
    """供应商失败原因里的不可见 DEL 不能进入审计链路。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "succeeded": False,
                "failure_reason": "target project\x7fis read-only",
            },
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
        )
        outcome = await gateway.submit_ticket(build_request())

    assert outcome.succeeded is False
    assert outcome.failure_reason == "target project is read-only"
    assert "\x7f" not in str(outcome.failure_reason)


async def test_http_failure_is_sanitized_without_secret_cause() -> None:
    """HTTP错误不能把供应商正文、请求头或令牌带入异常链。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "error": "private-ticketing-token and internal stack trace",
            },
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
        )
        with pytest.raises(TicketingGatewayError) as exc_info:
            await gateway.submit_ticket(build_request())

    assert str(exc_info.value) == "Ticketing gateway request failed"
    assert "private-ticketing-token" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"succeeded": "yes", "external_ticket_id": "JIRA-1001"},
        {"succeeded": True},
        {
            "succeeded": True,
            "external_ticket_id": "JIRA-1001",
            "failure_reason": "should not be present",
        },
        {"succeeded": False},
        {
            "succeeded": False,
            "failure_reason": "failed",
            "external_ticket_id": "JIRA-1001",
        },
        {
            "succeeded": True,
            "external_ticket_id": "JIRA-1001",
            "provider_debug": "unexpected field",
        },
    ],
)
async def test_invalid_gateway_contract_is_rejected(
    document: object,
) -> None:
    """结构漂移、半成功和额外字段都不能进入应用层。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
        )
        with pytest.raises(TicketingGatewayError):
            await gateway.submit_ticket(build_request())


async def test_invalid_json_is_rejected() -> None:
    """非 JSON 响应不能被当作外部提交结果。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
        )
        with pytest.raises(TicketingGatewayError, match="invalid JSON"):
            await gateway.submit_ticket(build_request())


async def test_oversized_response_is_stopped_during_streaming() -> None:
    """外部网关异常大响应必须在流式读取阶段被截断。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 1024,
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(max_response_bytes=128),
            http_client=http_client,
        )
        with pytest.raises(TicketingGatewayError, match="size"):
            await gateway.submit_ticket(build_request())


async def test_outer_cancellation_reaches_http_transport() -> None:
    """停机或租约取消必须穿透网关，不能伪装成外部系统错误。"""
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
        return httpx.Response(
            200,
            json={"succeeded": False, "failure_reason": "unused"},
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
        )
        task = asyncio.create_task(gateway.submit_ticket(build_request()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert cancelled is True


async def test_gateway_respects_http_client_ownership() -> None:
    """关闭网关不能误关共享 Client，自建连接池则必须释放。"""
    external_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "succeeded": True,
                    "external_ticket_id": "JIRA-1001",
                },
                request=request,
            )
        )
    )
    shared_gateway = HttpJsonTicketingGateway(
        build_config(),
        http_client=external_client,
    )

    await shared_gateway.close()

    assert external_client.is_closed is False
    await external_client.aclose()

    owned_gateway = HttpJsonTicketingGateway(build_config())
    owned_client = owned_gateway._http_client
    await owned_gateway.close()
    await owned_gateway.close()

    assert owned_client.is_closed is True
    with pytest.raises(TicketingGatewayError, match="closed"):
        await owned_gateway.submit_ticket(build_request())


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint_url": "https://user:pass@ticketing.example.com/api"},
        {"endpoint_url": "file:///tmp/ticketing"},
        {"endpoint_url": "https://ticketing.example.com/api?debug=true"},
        {"endpoint_url": "https://ticketing.example.com/api\nforged"},
        {"endpoint_url": "https://ticketing.example.com/api\x7fforged"},
        {"bearer_token": "plain-token"},
        {"bearer_token": SecretStr("bad\ntoken")},
        {"bearer_token": SecretStr("bad\ttoken")},
        {"bearer_token": SecretStr("bad\x7ftoken")},
        {"request_timeout_seconds": 0},
        {"request_timeout_seconds": float("inf")},
        {"max_response_bytes": True},
    ],
)
def test_config_rejects_unsafe_values(changes: dict[str, Any]) -> None:
    """危险地址、明文令牌和无界配置必须在启动前失败。"""
    with pytest.raises(AppValidationError):
        build_config(**changes)


def test_config_repr_masks_bearer_token() -> None:
    """配置调试输出应完全省略 bearer token 字段。"""
    representation = repr(build_config())

    assert "private-ticketing-token" not in representation
    assert "bearer_token" not in representation


async def test_request_type_is_validated_before_network_call() -> None:
    """错误调用方不能绕过端口契约触发外部请求。"""
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={}, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        gateway = HttpJsonTicketingGateway(
            build_config(),
            http_client=http_client,
        )
        with pytest.raises(AppValidationError):
            await gateway.submit_ticket(object())  # type: ignore[arg-type]

    assert called is False
