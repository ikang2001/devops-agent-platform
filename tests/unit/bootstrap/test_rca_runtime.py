from typing import Any

import pytest
from pydantic import SecretStr

from devops_agent_platform.agent.llm_report_generator import (
    ResilientLLMRCAReportGenerator,
)
from devops_agent_platform.agent.report_generator import (
    DeterministicRCAReportGenerator,
)
from devops_agent_platform.application.services.rca_consumer_runner import (
    RCAConsumerRunner,
)
from devops_agent_platform.bootstrap.rca_runtime import (
    build_rca_consumer_runtime,
)
from devops_agent_platform.bootstrap.worker_identity import (
    derive_suffixed_id,
    derive_worker_id,
)
from devops_agent_platform.infrastructure.config.settings import Settings
from devops_agent_platform.infrastructure.llm import (
    FailoverLLMReportGateway,
    OpenAICompatibleChatCompletionsGateway,
    OpenAICompatibleResponsesGateway,
)
from devops_agent_platform.infrastructure.observability import (
    LokiRangeClient,
    PrometheusRangeClient,
    TempoSearchClient,
)
from devops_agent_platform.ports.rca_report import (
    LLMReportGenerationOutcome,
)
from devops_agent_platform.tools.handlers import ChangeEventsQueryHandler


class RecordingReportObserver:
    """装配测试使用的报告观察端口。"""

    def observe_llm_report(
        self,
        outcome: LLMReportGenerationOutcome,
        duration_seconds: float,
    ) -> None:
        del outcome, duration_seconds


def build_settings(**changes: Any) -> Settings:
    """构造完整但不会主动连接外部服务的 RCA 配置。"""
    values = {
        "rca_consumer_enabled": True,
        "prometheus_base_url": "https://prometheus.example.com",
        "loki_base_url": "https://loki.example.com",
        "tempo_base_url": "https://tempo.example.com",
    }
    values.update(changes)
    return Settings(_env_file=None, **values)


def fake_session_factory():
    """装配测试不会执行数据库操作。"""
    raise AssertionError("session factory must not be called during assembly")


async def test_bundle_builds_complete_deterministic_consumer_graph() -> None:
    """默认报告模式应装配观测、拓扑和知识只读工具。"""
    bundle = build_rca_consumer_runtime(
        build_settings(),
        fake_session_factory,  # type: ignore[arg-type]
    )
    try:
        assert isinstance(bundle.worker, RCAConsumerRunner)
        assert tuple(type(item) for item in bundle.resources) == (
            PrometheusRangeClient,
            LokiRangeClient,
            TempoSearchClient,
        )
        workflow = (
            bundle.worker._consumer._processor._handler
            ._execution_coordinator._agent_workflow
        )
        assert len(workflow._plan.steps) == 5
        assert workflow._plan.plan_id == "default.observability-rca"
        assert workflow._config.continue_on_step_failure is False
        assert isinstance(
            workflow._report_generator,
            DeterministicRCAReportGenerator,
        )
        assert len(workflow._registry.list_tools()) == 7
        assert len(workflow._tool_executor._registry.list_handlers()) == 7
        change_registration = workflow._tool_executor._registry.get(
            "changes.query",
            "v1",
        )
        assert isinstance(
            change_registration.handler,
            ChangeEventsQueryHandler,
        )
    finally:
        for resource in reversed(bundle.resources):
            await resource.close()


async def test_bundle_applies_configured_metrics_contract() -> None:
    bundle = build_rca_consumer_runtime(
        build_settings(
            metrics_query_availability_metric="minishop_service_up",
            metrics_query_requests_metric="minishop_requests_total",
        ),
        fake_session_factory,  # type: ignore[arg-type]
    )
    try:
        workflow = (
            bundle.worker._consumer._processor._handler
            ._execution_coordinator._agent_workflow
        )
        registration = workflow._tool_executor._registry.get(
            "metrics.query",
            "v1",
        )
        assert (
            registration.handler._config.availability_metric
            == "minishop_service_up"
        )
        assert (
            registration.handler._config.requests_metric
            == "minishop_requests_total"
        )
    finally:
        for resource in reversed(bundle.resources):
            await resource.close()


async def test_bundle_wires_trace_free_partial_degrade_policy() -> None:
    """无 Trace 固定策略不要求 Tempo，并把 C0 开关注入工作流。"""
    bundle = build_rca_consumer_runtime(
        build_settings(
            rca_investigation_policy="fixed_no_traces",
            rca_continue_on_step_failure=True,
            tempo_base_url=None,
        ),
        fake_session_factory,  # type: ignore[arg-type]
    )
    try:
        assert tuple(type(item) for item in bundle.resources) == (
            PrometheusRangeClient,
            LokiRangeClient,
        )
        workflow = (
            bundle.worker._consumer._processor._handler
            ._execution_coordinator._agent_workflow
        )
        assert workflow._plan.plan_id == "observability-rca.no-traces"
        assert [step.tool_name for step in workflow._plan.steps] == [
            "metrics.query",
            "changes.query",
            "logs.query",
            "runbooks.retrieve",
        ]
        assert workflow._config.continue_on_step_failure is True
        assert len(workflow._registry.list_tools()) == 6
        assert len(workflow._tool_executor._registry.list_handlers()) == 6
    finally:
        for resource in reversed(bundle.resources):
            await resource.close()


async def test_bundle_optionally_wires_resilient_llm_generator() -> None:
    """显式启用后才增加模型网关资源和弹性报告生成器。"""
    observer = RecordingReportObserver()
    bundle = build_rca_consumer_runtime(
        build_settings(
            llm_report_enabled=True,
            llm_base_url="https://api.example.com",
            llm_api_key=SecretStr("private-key"),
            llm_model="rca-model-v1",
        ),
        fake_session_factory,  # type: ignore[arg-type]
        report_observer=observer,
    )
    try:
        assert len(bundle.resources) == 4
        assert isinstance(
            bundle.resources[-1],
            OpenAICompatibleResponsesGateway,
        )
        workflow = (
            bundle.worker._consumer._processor._handler
            ._execution_coordinator._agent_workflow
        )
        assert isinstance(
            workflow._report_generator,
            ResilientLLMRCAReportGenerator,
        )
        assert workflow._report_generator._observer is observer
    finally:
        for resource in reversed(bundle.resources):
            await resource.close()


async def test_bundle_wires_dashscope_chat_completions_gateway() -> None:
    bundle = build_rca_consumer_runtime(
        build_settings(
            llm_report_enabled=True,
            llm_provider="dashscope",
            llm_api_style="chat_completions",
            llm_api_key=SecretStr("private-key"),
            llm_model="qwen3.7-plus",
        ),
        fake_session_factory,  # type: ignore[arg-type]
    )
    try:
        assert len(bundle.resources) == 4
        assert isinstance(
            bundle.resources[-1],
            OpenAICompatibleChatCompletionsGateway,
        )
        assert (
            bundle.resources[-1]._config.base_url
            == "https://dashscope.aliyuncs.com/compatible-mode"
        )
    finally:
        for resource in reversed(bundle.resources):
            await resource.close()


async def test_bundle_wires_ordered_llm_failover_chain() -> None:
    bundle = build_rca_consumer_runtime(
        build_settings(
            llm_report_enabled=True,
            llm_provider_order="openai,dashscope",
            llm_openai_api_key=SecretStr("private-openai-key"),
            llm_openai_model="gpt-approved",
            llm_dashscope_api_key=SecretStr("private-dashscope-key"),
            llm_dashscope_model="qwen3.7-plus",
        ),
        fake_session_factory,  # type: ignore[arg-type]
    )
    try:
        assert len(bundle.resources) == 4
        assert isinstance(bundle.resources[-1], FailoverLLMReportGateway)
        assert tuple(
            attempt.provider_name
            for attempt in bundle.resources[-1]._attempts
        ) == ("openai", "dashscope")
        assert isinstance(
            bundle.resources[-1]._attempts[0].gateway,
            OpenAICompatibleResponsesGateway,
        )
        assert isinstance(
            bundle.resources[-1]._attempts[1].gateway,
            OpenAICompatibleChatCompletionsGateway,
        )
    finally:
        for resource in reversed(bundle.resources):
            await resource.close()


async def test_bundle_uses_single_provider_without_failover_wrapper() -> None:
    bundle = build_rca_consumer_runtime(
        build_settings(
            llm_report_enabled=True,
            llm_provider_order="openai,dashscope",
            llm_openai_api_key=SecretStr("private-openai-key"),
            llm_openai_model="gpt-approved",
        ),
        fake_session_factory,  # type: ignore[arg-type]
    )
    try:
        assert len(bundle.resources) == 4
        assert isinstance(
            bundle.resources[-1],
            OpenAICompatibleResponsesGateway,
        )
        assert bundle.resources[-1]._config.base_url == "https://api.openai.com"
    finally:
        for resource in reversed(bundle.resources):
            await resource.close()


def test_bundle_rejects_disabled_consumer() -> None:
    """其它启动入口不能绕过 Settings 状态直接构造 Worker。"""
    with pytest.raises(ValueError, match="not enabled"):
        build_rca_consumer_runtime(
            Settings(_env_file=None),
            fake_session_factory,  # type: ignore[arg-type]
        )


async def test_bundle_derives_long_worker_id_with_hash_suffix() -> None:
    """默认Worker ID过长时不能静默截断掉身份语义。"""
    client_id = "rca-" + "a" * 124
    bundle = build_rca_consumer_runtime(
        build_settings(rca_consumer_client_id=client_id),
        fake_session_factory,  # type: ignore[arg-type]
    )
    try:
        worker_id = bundle.worker._worker_id
        assert worker_id == derive_worker_id(client_id)
        assert len(worker_id) == 128
        assert worker_id.endswith("-worker")
        assert worker_id != f"{client_id}-worker"[:128]
        dead_letter_client_id = (
            bundle.worker._consumer._dead_letter_publisher._config.client_id
        )
        assert dead_letter_client_id == derive_suffixed_id(client_id, "dlq")
        assert len(dead_letter_client_id) == 128
        assert dead_letter_client_id.endswith("-dlq")
        assert dead_letter_client_id != f"{client_id[:119]}-dlq"
    finally:
        for resource in reversed(bundle.resources):
            await resource.close()
