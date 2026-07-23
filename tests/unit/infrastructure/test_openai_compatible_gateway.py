import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import LLMProviderError
from devops_agent_platform.domain.enums import EvidenceType
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.llm import (
    FailoverLLMReportGateway,
    LLMProviderAttempt,
    OpenAICompatibleChatCompletionsConfig,
    OpenAICompatibleChatCompletionsGateway,
    OpenAICompatibleResponsesConfig,
    OpenAICompatibleResponsesGateway,
)
from devops_agent_platform.ports.llm import (
    LLMReportEvidence,
    LLMReportRequest,
)


def build_request() -> LLMReportRequest:
    """构造经过应用层最小化后的模型报告请求。"""
    return LLMReportRequest(
        tenant_id="tenant_001",
        incident_id="inc_001",
        workflow_run_id="wfr_001",
        execution_attempt=1,
        trace_id="trc_001",
        prompt_version="rca-report-v1",
        evidence=(
            LLMReportEvidence(
                evidence_id="a" * 64,
                evidence_type=EvidenceType.LOG,
                source="loki",
                summary="Database timeout rate increased.",
                confidence=0.9,
            ),
        ),
    )


def valid_report() -> dict[str, Any]:
    """构造模型 output_text 中的合法报告对象。"""
    return {
        "conclusion_status": "CANDIDATE",
        "title": "Database latency candidate",
        "summary": "Logs show increased database timeout rates.",
        "confidence": 0.8,
        "evidence_ids": ["a" * 64],
        "recommendations": ["Review connection pool saturation."],
    }


class FakeLLMGateway:
    def __init__(
        self,
        *,
        result: Mapping[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or valid_report()
        self.error = error
        self.calls = 0
        self.closed = False

    async def generate_report(
        self,
        request: LLMReportRequest,
    ) -> Mapping[str, Any]:
        del request
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    async def close(self) -> None:
        self.closed = True


def responses_document(
    report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 OpenAI Responses API 的完成响应。"""
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(report or valid_report()),
                        "annotations": [],
                    }
                ],
            }
        ],
    }


def chat_completions_document(
    report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(report or valid_report()),
                },
                "finish_reason": "stop",
            }
        ]
    }


def build_config(
    **changes: Any,
) -> OpenAICompatibleResponsesConfig:
    """构造不暴露密钥的网关配置。"""
    values = {
        "base_url": "https://api.example.com",
        "api_key": SecretStr("private-api-key"),
        "model": "rca-model-v1",
    }
    values.update(changes)
    return OpenAICompatibleResponsesConfig(**values)


def build_chat_config(
    **changes: Any,
) -> OpenAICompatibleChatCompletionsConfig:
    values = {
        "base_url": "https://api.example.com",
        "api_key": SecretStr("private-api-key"),
        "model": "rca-model-v1",
    }
    values.update(changes)
    return OpenAICompatibleChatCompletionsConfig(**values)


async def test_failover_gateway_uses_next_provider_after_provider_error() -> None:
    first = FakeLLMGateway(error=LLMProviderError("private provider detail"))
    second = FakeLLMGateway(result=valid_report())
    gateway = FailoverLLMReportGateway(
        (
            LLMProviderAttempt("openai", first),
            LLMProviderAttempt("dashscope", second),
        )
    )

    result = await gateway.generate_report(build_request())

    assert result == valid_report()
    assert first.calls == 1
    assert second.calls == 1


async def test_failover_gateway_propagates_cancellation_without_retry() -> None:
    first = FakeLLMGateway(error=asyncio.CancelledError())
    second = FakeLLMGateway(result=valid_report())
    gateway = FailoverLLMReportGateway(
        (
            LLMProviderAttempt("openai", first),
            LLMProviderAttempt("dashscope", second),
        )
    )

    with pytest.raises(asyncio.CancelledError):
        await gateway.generate_report(build_request())

    assert first.calls == 1
    assert second.calls == 0


async def test_failover_gateway_sanitizes_all_provider_failures() -> None:
    gateway = FailoverLLMReportGateway(
        (
            LLMProviderAttempt(
                "openai",
                FakeLLMGateway(error=LLMProviderError("private-provider-1")),
            ),
            LLMProviderAttempt(
                "dashscope",
                FakeLLMGateway(error=LLMProviderError("private-provider-2")),
            ),
        )
    )

    with pytest.raises(LLMProviderError) as exc_info:
        await gateway.generate_report(build_request())

    assert str(exc_info.value) == "all LLM providers are unavailable"
    assert "private-provider" not in str(exc_info.value)


async def test_failover_gateway_closes_all_children() -> None:
    first = FakeLLMGateway()
    second = FakeLLMGateway()
    gateway = FailoverLLMReportGateway(
        (
            LLMProviderAttempt("openai", first),
            LLMProviderAttempt("dashscope", second),
        )
    )

    await gateway.close()
    await gateway.close()

    assert first.closed is True
    assert second.closed is True
    with pytest.raises(LLMProviderError, match="closed"):
        await gateway.generate_report(build_request())


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("tenant_id", "tenant_001\nforged"),
        ("incident_id", "inc_001\rforged"),
        ("workflow_run_id", "wfr_001\tforged"),
        ("trace_id", "trc_001\x7fforged"),
        ("trace_id", "trc_001\nforged"),
        ("prompt_version", "rca-report-v1\tforged"),
    ],
)
def test_llm_report_request_rejects_control_character_identity(
    field_name: str,
    value: str,
) -> None:
    """模型请求身份字段会进追踪和审计链路，必须保持单行。"""
    with pytest.raises(AppValidationError, match="control characters"):
        replace(build_request(), **{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("evidence_id", "evd_001\nforged"),
        ("source", "loki\x7fforged"),
        ("source", "loki\rforged"),
        ("summary", "Database timeout\tforged"),
    ],
)
def test_llm_report_evidence_rejects_control_character_projection(
    field_name: str,
    value: str,
) -> None:
    """外发给模型的 Evidence 投影不能携带控制字符污染 Prompt JSON。"""
    evidence = build_request().evidence[0]

    with pytest.raises(AppValidationError, match="control characters"):
        replace(evidence, **{field_name: value})


async def test_gateway_sends_strict_minimal_responses_request() -> None:
    """网关应发送最小摘要、动态白名单 Schema 和链路标识。"""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=responses_document(),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = OpenAICompatibleResponsesGateway(
            build_config(),
            http_client=http_client,
        )
        result = await gateway.generate_report(build_request())

    assert result == valid_report()
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/responses"
    assert request.headers["Authorization"] == "Bearer private-api-key"
    assert request.headers["X-Trace-Id"] == "trc_001"
    body = json.loads(request.content)
    assert body["model"] == "rca-model-v1"
    assert body["store"] is False
    assert body["text"]["format"]["strict"] is True
    schema = body["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["evidence_ids"]["items"]["enum"] == ["a" * 64]
    assert schema["properties"]["conclusion_status"]["enum"] == [
        "UNDETERMINED",
        "CANDIDATE",
    ]
    model_input = json.loads(body["input"])
    assert set(model_input) == {"prompt_version", "evidence"}
    assert "tenant_001" not in body["input"]
    assert "workflow_run_id" not in body["input"]


async def test_responses_gateway_does_not_duplicate_existing_v1_path() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=responses_document(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = OpenAICompatibleResponsesGateway(
            build_config(base_url="https://dashscope.example.com/compatible-mode/v1"),
            http_client=http_client,
        )
        await gateway.generate_report(build_request())

    assert requests[0].url.path == "/compatible-mode/v1/responses"


async def test_chat_completions_gateway_sends_structured_request() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=chat_completions_document(),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = OpenAICompatibleChatCompletionsGateway(
            build_chat_config(base_url="https://api.example.com/v1"),
            http_client=http_client,
        )
        result = await gateway.generate_report(build_request())

    assert result == valid_report()
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer private-api-key"
    body = json.loads(request.content)
    assert body["model"] == "rca-model-v1"
    assert body["max_tokens"] == 2048
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert "tenant_001" not in body["messages"][1]["content"]
    json_schema = body["response_format"]["json_schema"]
    assert json_schema["strict"] is True
    assert json_schema["schema"]["additionalProperties"] is False


async def test_http_failure_is_sanitized_without_secret_cause() -> None:
    """HTTP错误不能携带供应商正文、底层请求或鉴权头进入异常链。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": "private-api-key and internal provider detail"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = OpenAICompatibleResponsesGateway(
            build_config(),
            http_client=http_client,
        )
        with pytest.raises(LLMProviderError) as exc_info:
            await gateway.generate_report(build_request())

    assert str(exc_info.value) == "LLM request failed"
    assert "private-api-key" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    "document",
    [
        {"status": "incomplete", "output": []},
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "refusal",
                            "refusal": "provider policy detail",
                        }
                    ],
                }
            ],
        },
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "{}"},
                        {"type": "output_text", "text": "{}"},
                    ],
                }
            ],
        },
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "not-json",
                        }
                    ],
                }
            ],
        },
    ],
)
async def test_invalid_provider_contract_is_rejected(
    document: Mapping[str, Any],
) -> None:
    """未完成、拒绝、多文本和非JSON响应都不能进入报告生成器。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = OpenAICompatibleResponsesGateway(
            build_config(),
            http_client=http_client,
        )
        with pytest.raises(LLMProviderError):
            await gateway.generate_report(build_request())


async def test_oversized_response_is_stopped_during_streaming() -> None:
    """供应商异常大响应必须在流式读取阶段被截断。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 1024,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = OpenAICompatibleResponsesGateway(
            build_config(max_response_bytes=128),
            http_client=http_client,
        )
        with pytest.raises(LLMProviderError, match="size"):
            await gateway.generate_report(build_request())


async def test_outer_cancellation_reaches_http_transport() -> None:
    """租约丢失或停机取消必须穿透网关，不能转换成供应商错误。"""
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
        return httpx.Response(200, json=responses_document(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = OpenAICompatibleResponsesGateway(
            build_config(),
            http_client=http_client,
        )
        task = asyncio.create_task(gateway.generate_report(build_request()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert cancelled is True


async def test_gateway_respects_http_client_ownership() -> None:
    """关闭网关不能误关共享Client，自建连接池则必须被释放。"""
    external_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=responses_document(),
                request=request,
            )
        )
    )
    shared_gateway = OpenAICompatibleResponsesGateway(
        build_config(),
        http_client=external_client,
    )

    await shared_gateway.close()

    assert external_client.is_closed is False
    await external_client.aclose()

    owned_gateway = OpenAICompatibleResponsesGateway(build_config())
    owned_client = owned_gateway._http_client
    await owned_gateway.close()
    await owned_gateway.close()

    assert owned_client.is_closed is True
    with pytest.raises(LLMProviderError, match="closed"):
        await owned_gateway.generate_report(build_request())


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "https://user:pass@api.example.com"},
        {"base_url": "https://api.example.com\nforged"},
        {"base_url": "https://api.example.com/\x7fforged"},
        {"base_url": "file:///tmp/model"},
        {"api_key": SecretStr(" ")},
        {"api_key": SecretStr("bad\ttoken")},
        {"api_key": SecretStr("bad\x7ftoken")},
        {"api_key": "plain-text-secret"},
        {"model": "bad\nmodel"},
        {"model": "bad\tmodel"},
        {"model": "bad\x7fmodel"},
        {"request_timeout_seconds": float("inf")},
        {"max_response_bytes": 0},
        {"max_output_tokens": True},
    ],
)
def test_config_rejects_unsafe_values(changes: dict[str, Any]) -> None:
    """危险地址、明文凭据和无界配置必须在启动前失败。"""
    with pytest.raises(AppValidationError):
        build_config(**changes)


def test_config_repr_masks_api_key() -> None:
    """配置调试输出应完全省略 API Key 字段。"""
    representation = repr(build_config())

    assert "private-api-key" not in representation
    assert "api_key" not in representation
