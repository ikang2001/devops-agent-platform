from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.agent import (
    ControlledAgentWorkflow,
    LLMRCAReportGeneratorConfig,
    ResilientLLMRCAReportGenerator,
    build_default_observability_plan,
)
from devops_agent_platform.application.services.rca_consumer_runner import (
    RCAConsumerRunner,
)
from devops_agent_platform.application.services.rca_execution_coordinator import (
    RCAExecutionCoordinator,
    RCAExecutionCoordinatorConfig,
)
from devops_agent_platform.application.services.rca_record_processor import (
    RCARequestedRecordProcessor,
)
from devops_agent_platform.application.services.rca_requested_handler import (
    RCARequestedMessageHandler,
)
from devops_agent_platform.application.services.workflow_execution_service import (
    WorkflowClaimConfig,
    WorkflowExecutionApplicationService,
)
from devops_agent_platform.bootstrap.worker_identity import (
    derive_suffixed_id,
    derive_worker_id,
)
from devops_agent_platform.infrastructure.adapters.kafka import (
    KafkaConsumerConfig,
    KafkaDeadLetterPublisher,
    KafkaPublisherConfig,
    RCAKafkaConsumer,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyObservabilityTargetResolver,
    SQLAlchemyRunbookSearch,
    SQLAlchemyToolPermissionProvider,
    SQLAlchemyUnitOfWork,
)
from devops_agent_platform.infrastructure.config.settings import Settings
from devops_agent_platform.infrastructure.llm import (
    build_llm_report_gateway_from_configs,
)
from devops_agent_platform.infrastructure.observability import (
    LokiRangeClient,
    LokiRangeClientConfig,
    PrometheusRangeClient,
    PrometheusRangeClientConfig,
    TempoSearchClient,
    TempoSearchClientConfig,
)
from devops_agent_platform.ports.rca_report import (
    LLMReportGenerationObserverPort,
)
from devops_agent_platform.tools.executor import ToolExecutor
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.handlers import (
    LokiLogsQueryHandler,
    PrometheusMetricsQueryHandler,
    PrometheusMetricsQueryHandlerConfig,
    RunbookRetrievalHandler,
    TempoTracesQueryHandler,
    register_loki_logs_tool,
    register_prometheus_metrics_tool,
    register_runbook_retrieval_tool,
    register_tempo_traces_tool,
)
from devops_agent_platform.tools.permission import ToolPermissionChecker
from devops_agent_platform.tools.registry import ToolRegistry


class AsyncCloseable(Protocol):
    """Runtime 在后台 Worker 停止后需要关闭的异步资源。"""

    async def close(self) -> None:
        """释放资源；实现必须支持重复调用。"""
        ...


@dataclass(frozen=True)
class RCAConsumerRuntimeBundle:
    """RCA Consumer Worker 及其独占网络资源集合。"""

    worker: RCAConsumerRunner
    resources: tuple[AsyncCloseable, ...]


def build_rca_consumer_runtime(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    report_observer: LLMReportGenerationObserverPort | None = None,
) -> RCAConsumerRuntimeBundle:
    """装配消息消费、租约协调、只读工具和可选 LLM 报告链路。

    调用方必须只在 ``rca_consumer_enabled`` 为真时调用。函数本身仍执行防御性
    检查，避免测试或其它启动入口绕过 Settings 的组合约束。
    """
    if not settings.rca_consumer_enabled:
        raise ValueError("RCA consumer is not enabled")

    prometheus_config = PrometheusRangeClientConfig(
        base_url=settings.prometheus_base_url or "",
        bearer_token=settings.prometheus_bearer_token,
    )
    loki_config = LokiRangeClientConfig(
        base_url=settings.loki_base_url or "",
        bearer_token=settings.loki_bearer_token,
    )
    tempo_config = TempoSearchClientConfig(
        base_url=settings.tempo_base_url or "",
        bearer_token=settings.tempo_bearer_token,
    )
    kafka_password = (
        settings.kafka_sasl_password.get_secret_value()
        if settings.kafka_sasl_password is not None
        else None
    )
    consumer_config = KafkaConsumerConfig(
        bootstrap_servers=settings.kafka_servers,
        topic=settings.kafka_topic,
        group_id=settings.rca_consumer_group_id,
        client_id=settings.rca_consumer_client_id,
        security_protocol=settings.kafka_security_protocol,
        sasl_mechanism=settings.kafka_sasl_mechanism,
        sasl_username=settings.kafka_sasl_username,
        sasl_password=kafka_password,
        poll_timeout_ms=settings.rca_consumer_poll_timeout_ms,
        lag_query_timeout_ms=settings.rca_consumer_lag_timeout_ms,
        max_lag_partitions=(
            settings.rca_consumer_max_lag_partitions
        ),
        lag_sample_interval_seconds=(
            settings.rca_consumer_lag_sample_interval_seconds
        ),
    )
    dead_letter_config = KafkaPublisherConfig(
        bootstrap_servers=settings.kafka_servers,
        topic=settings.rca_dead_letter_topic,
        client_id=derive_suffixed_id(
            settings.rca_consumer_client_id,
            "dlq",
        ),
        security_protocol=settings.kafka_security_protocol,
        sasl_mechanism=settings.kafka_sasl_mechanism,
        sasl_username=settings.kafka_sasl_username,
        sasl_password=kafka_password,
    )
    worker_id = (
        settings.rca_consumer_worker_id
        or derive_worker_id(settings.rca_consumer_client_id)
    )
    llm_gateway = None
    llm_generator_config: LLMRCAReportGeneratorConfig | None = None
    if settings.llm_report_enabled:
        llm_gateway = build_llm_report_gateway_from_configs(
            settings.llm_provider_configs,
            request_timeout_seconds=settings.llm_request_timeout_seconds,
            max_response_bytes=settings.llm_max_response_bytes,
            max_output_tokens=settings.llm_max_output_tokens,
        )
        llm_generator_config = LLMRCAReportGeneratorConfig(
            timeout_seconds=settings.llm_request_timeout_seconds,
            failure_threshold=settings.llm_failure_threshold,
            recovery_timeout_seconds=settings.llm_recovery_timeout_seconds,
        )

    prometheus = PrometheusRangeClient(prometheus_config)
    loki = LokiRangeClient(loki_config)
    tempo = TempoSearchClient(tempo_config)
    resources: list[AsyncCloseable] = [prometheus, loki, tempo]

    target_resolver = SQLAlchemyObservabilityTargetResolver(session_factory)
    runbook_search = SQLAlchemyRunbookSearch(session_factory)
    handler_registry = ToolHandlerRegistry()
    definitions = (
        register_prometheus_metrics_tool(
            handler_registry,
            PrometheusMetricsQueryHandler(
                target_resolver,
                prometheus,
                PrometheusMetricsQueryHandlerConfig(
                    tenant_label=settings.metrics_query_tenant_label,
                    service_label=settings.metrics_query_service_label,
                    status_label=settings.metrics_query_status_label,
                    requests_metric=settings.metrics_query_requests_metric,
                    latency_bucket_metric=(
                        settings.metrics_query_latency_bucket_metric
                    ),
                    availability_metric=(
                        settings.metrics_query_availability_metric
                    ),
                ),
            ),
        ),
        register_loki_logs_tool(
            handler_registry,
            LokiLogsQueryHandler(target_resolver, loki),
        ),
        register_tempo_traces_tool(
            handler_registry,
            TempoTracesQueryHandler(target_resolver, tempo),
        ),
        register_runbook_retrieval_tool(
            handler_registry,
            RunbookRetrievalHandler(
                target_resolver,
                runbook_search,
            ),
        ),
    )
    report_generator = None
    if llm_gateway is not None and llm_generator_config is not None:
        resources.append(llm_gateway)
        report_generator = ResilientLLMRCAReportGenerator(
            llm_gateway,
            config=llm_generator_config,
            observer=report_observer,
        )

    workflow = ControlledAgentWorkflow(
        plan=build_default_observability_plan(),
        registry=ToolRegistry(definitions),
        permission_checker=ToolPermissionChecker(
            SQLAlchemyToolPermissionProvider(session_factory)
        ),
        tool_executor=ToolExecutor(handler_registry),
        report_generator=report_generator,
    )

    def unit_of_work_factory() -> SQLAlchemyUnitOfWork:
        """为抢占、心跳和完成分别创建短事务。"""
        return SQLAlchemyUnitOfWork(session_factory)

    execution_service = WorkflowExecutionApplicationService(
        unit_of_work_factory=unit_of_work_factory,
        config=WorkflowClaimConfig(
            lease_duration=timedelta(
                seconds=settings.rca_consumer_lease_seconds
            )
        ),
    )
    coordinator = RCAExecutionCoordinator(
        agent_workflow=workflow,
        execution_control=execution_service,
        config=RCAExecutionCoordinatorConfig(
            heartbeat_interval=timedelta(
                seconds=settings.rca_consumer_heartbeat_seconds
            ),
            execution_timeout=timedelta(
                seconds=settings.rca_consumer_execution_timeout_seconds
            ),
        ),
    )
    handler = RCARequestedMessageHandler(
        workflow_execution_service=execution_service,
        execution_coordinator=coordinator,
        worker_id=worker_id,
    )
    consumer = RCAKafkaConsumer(
        config=consumer_config,
        processor=RCARequestedRecordProcessor(handler),
        dead_letter_publisher=KafkaDeadLetterPublisher(
            dead_letter_config
        ),
    )
    return RCAConsumerRuntimeBundle(
        worker=RCAConsumerRunner(
            consumer=consumer,
            worker_id=worker_id,
        ),
        resources=tuple(resources),
    )
