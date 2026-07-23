from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.services import (
    ticket_submission_consumer_runner as consumer_runner_module,
)
from devops_agent_platform.application.services import (
    ticket_submission_record_processor as record_processor_module,
)
from devops_agent_platform.application.services import (
    ticket_submission_requested_handler as handler_module,
)
from devops_agent_platform.application.services.ticket_submission_service import (
    TicketSubmissionApplicationService,
)
from devops_agent_platform.bootstrap.worker_identity import (
    derive_suffixed_id,
    derive_worker_id,
)
from devops_agent_platform.infrastructure.adapters.kafka import (
    KafkaConsumerConfig,
    KafkaDeadLetterPublisher,
    KafkaPublisherConfig,
    TicketSubmissionKafkaConsumer,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyUnitOfWork,
)
from devops_agent_platform.infrastructure.config.settings import Settings
from devops_agent_platform.infrastructure.identifiers import UUIDIdentifierGenerator
from devops_agent_platform.ports.ticketing import TicketingGatewayPort

TicketSubmissionConsumerRunner = (
    consumer_runner_module.TicketSubmissionConsumerRunner
)
TicketSubmissionRequestedRecordProcessor = (
    record_processor_module.TicketSubmissionRequestedRecordProcessor
)
TicketSubmissionRequestedMessageHandler = (
    handler_module.TicketSubmissionRequestedMessageHandler
)


@dataclass(frozen=True)
class TicketSubmissionConsumerRuntimeBundle:
    """外部工单提交 Consumer Worker 装配结果。"""

    worker: TicketSubmissionConsumerRunner


def build_ticket_submission_consumer_runtime(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    ticketing_gateway: TicketingGatewayPort | None,
) -> TicketSubmissionConsumerRuntimeBundle:
    """装配外部工单提交消息消费链路。

    调用方必须只在 ``ticket_submission_consumer_enabled`` 为真时调用。
    同时，真实外部工单系统适配器必须通过 ``ticketing_gateway`` 显式注入；
    没有网关时启动阶段直接失败，避免 Worker 在线上固定重试同一条消息。
    """
    if not settings.ticket_submission_consumer_enabled:
        raise ValueError("Ticket submission consumer is not enabled")
    if ticketing_gateway is None:
        raise ValueError("Ticketing gateway is required")

    kafka_password = (
        settings.kafka_sasl_password.get_secret_value()
        if settings.kafka_sasl_password is not None
        else None
    )
    worker_id = (
        settings.ticket_submission_consumer_worker_id
        or derive_worker_id(settings.ticket_submission_consumer_client_id)
    )
    consumer_config = KafkaConsumerConfig(
        bootstrap_servers=settings.kafka_servers,
        topic=settings.kafka_topic,
        group_id=settings.ticket_submission_consumer_group_id,
        client_id=settings.ticket_submission_consumer_client_id,
        security_protocol=settings.kafka_security_protocol,
        sasl_mechanism=settings.kafka_sasl_mechanism,
        sasl_username=settings.kafka_sasl_username,
        sasl_password=kafka_password,
        poll_timeout_ms=settings.ticket_submission_consumer_poll_timeout_ms,
    )
    dead_letter_config = KafkaPublisherConfig(
        bootstrap_servers=settings.kafka_servers,
        topic=settings.ticket_submission_dead_letter_topic,
        client_id=derive_suffixed_id(
            settings.ticket_submission_consumer_client_id,
            "dlq",
        ),
        security_protocol=settings.kafka_security_protocol,
        sasl_mechanism=settings.kafka_sasl_mechanism,
        sasl_username=settings.kafka_sasl_username,
        sasl_password=kafka_password,
    )

    def unit_of_work_factory() -> SQLAlchemyUnitOfWork:
        """为消息读取上下文和结果回填分别创建短事务。"""
        return SQLAlchemyUnitOfWork(session_factory)

    submission_service = TicketSubmissionApplicationService(
        unit_of_work_factory=unit_of_work_factory,
        identifier_generator=UUIDIdentifierGenerator(),
    )
    handler = TicketSubmissionRequestedMessageHandler(
        unit_of_work_factory=unit_of_work_factory,
        ticketing_gateway=ticketing_gateway,
        submission_service=submission_service,
        worker_id=worker_id,
    )
    consumer = TicketSubmissionKafkaConsumer(
        config=consumer_config,
        processor=TicketSubmissionRequestedRecordProcessor(handler),
        dead_letter_publisher=KafkaDeadLetterPublisher(dead_letter_config),
    )
    return TicketSubmissionConsumerRuntimeBundle(
        worker=TicketSubmissionConsumerRunner(
            consumer=consumer,
            worker_id=worker_id,
        )
    )
