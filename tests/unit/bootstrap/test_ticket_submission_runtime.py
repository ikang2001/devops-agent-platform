from typing import Any

import pytest

from devops_agent_platform.application.services import (
    ticket_submission_consumer_runner as consumer_runner_module,
)
from devops_agent_platform.bootstrap.ticket_submission_runtime import (
    build_ticket_submission_consumer_runtime,
)
from devops_agent_platform.bootstrap.worker_identity import (
    derive_suffixed_id,
    derive_worker_id,
)
from devops_agent_platform.infrastructure.adapters.kafka import (
    TicketSubmissionKafkaConsumer,
)
from devops_agent_platform.infrastructure.config.settings import Settings
from devops_agent_platform.ports.ticketing import (
    TicketingSubmitOutcome,
    TicketingSubmitRequest,
)

TicketSubmissionConsumerRunner = (
    consumer_runner_module.TicketSubmissionConsumerRunner
)


class FakeTicketingGateway:
    """装配测试使用的外部工单端口替身。"""

    async def submit_ticket(
        self,
        request: TicketingSubmitRequest,
    ) -> TicketingSubmitOutcome:
        del request
        raise AssertionError("gateway must not be called during assembly")


def build_settings(**changes: Any) -> Settings:
    """构造完整但不会主动连接外部服务的工单提交消费配置。"""
    values = {
        "ticket_submission_consumer_enabled": True,
    }
    values.update(changes)
    return Settings(_env_file=None, **values)


def fake_session_factory():
    """装配测试不会执行数据库操作。"""
    raise AssertionError("session factory must not be called during assembly")


def test_bundle_builds_ticket_submission_consumer_graph() -> None:
    """显式注入工单网关后才装配完整消费链路。"""
    bundle = build_ticket_submission_consumer_runtime(
        build_settings(),
        fake_session_factory,  # type: ignore[arg-type]
        ticketing_gateway=FakeTicketingGateway(),
    )

    assert isinstance(bundle.worker, TicketSubmissionConsumerRunner)
    consumer = bundle.worker._consumer
    assert isinstance(consumer, TicketSubmissionKafkaConsumer)
    assert consumer._config.group_id == "devops-agent-ticket-submission-v1"
    assert consumer._config.client_id == (
        "devops-agent-ticket-submission-consumer"
    )
    assert consumer._dead_letter_publisher._config.topic == (
        "devops-agent.ticket-submission.dead-letter.v1"
    )
    assert bundle.worker._worker_id == (
        "devops-agent-ticket-submission-consumer-worker"
    )
    handler = consumer._processor._handler
    assert handler._ticketing_gateway.__class__ is FakeTicketingGateway


def test_bundle_uses_explicit_worker_and_consumer_settings() -> None:
    """显式消费组、客户端、死信Topic和Worker ID不能被静默改写。"""
    bundle = build_ticket_submission_consumer_runtime(
        build_settings(
            ticket_submission_consumer_worker_id="ticket-worker-001",
            ticket_submission_consumer_group_id="ticket-group",
            ticket_submission_consumer_client_id="ticket-client",
            ticket_submission_dead_letter_topic="ticket.dlq",
            ticket_submission_consumer_poll_timeout_ms=250,
        ),
        fake_session_factory,  # type: ignore[arg-type]
        ticketing_gateway=FakeTicketingGateway(),
    )

    consumer = bundle.worker._consumer
    assert consumer._config.group_id == "ticket-group"
    assert consumer._config.client_id == "ticket-client"
    assert consumer._config.poll_timeout_ms == 250
    assert consumer._dead_letter_publisher._config.topic == "ticket.dlq"
    assert bundle.worker._worker_id == "ticket-worker-001"


def test_bundle_rejects_disabled_consumer() -> None:
    """其它启动入口不能绕过 Settings 状态直接构造 Worker。"""
    with pytest.raises(ValueError, match="not enabled"):
        build_ticket_submission_consumer_runtime(
            Settings(_env_file=None),
            fake_session_factory,  # type: ignore[arg-type]
            ticketing_gateway=FakeTicketingGateway(),
        )


def test_bundle_requires_real_ticketing_gateway() -> None:
    """启用消费链路时必须显式注入外部工单系统适配器。"""
    with pytest.raises(ValueError, match="gateway"):
        build_ticket_submission_consumer_runtime(
            build_settings(),
            fake_session_factory,  # type: ignore[arg-type]
            ticketing_gateway=None,
        )


def test_bundle_derives_long_ticket_worker_id_with_hash_suffix() -> None:
    """工单Consumer默认Worker ID过长时也保留可审计后缀。"""
    client_id = "ticket-" + "b" * 121
    bundle = build_ticket_submission_consumer_runtime(
        build_settings(ticket_submission_consumer_client_id=client_id),
        fake_session_factory,  # type: ignore[arg-type]
        ticketing_gateway=FakeTicketingGateway(),
    )

    worker_id = bundle.worker._worker_id
    assert worker_id == derive_worker_id(client_id)
    assert len(worker_id) == 128
    assert worker_id.endswith("-worker")
    assert worker_id != f"{client_id}-worker"[:128]
    consumer = bundle.worker._consumer
    dead_letter_client_id = (
        consumer._dead_letter_publisher._config.client_id
    )
    assert dead_letter_client_id == derive_suffixed_id(client_id, "dlq")
    assert len(dead_letter_client_id) == 128
    assert dead_letter_client_id.endswith("-dlq")
    assert dead_letter_client_id != f"{client_id[:119]}-dlq"
