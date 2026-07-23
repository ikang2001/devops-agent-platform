import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.events import (
    ClaimedOutboxEvent,
    OutboxEvent,
)
from devops_agent_platform.application.services.outbox_dispatcher import (
    OutboxDispatcher,
    OutboxDispatcherConfig,
)
from devops_agent_platform.domain.exceptions import AppValidationError

NOW = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)


def build_claimed(
    event_id: str,
    attempts: int = 1,
) -> ClaimedOutboxEvent:
    """构造已被Worker抢占的事件。"""
    return ClaimedOutboxEvent(
        event=OutboxEvent(
            event_id=event_id,
            tenant_id="tenant_001",
            aggregate_type="Incident",
            aggregate_id="inc_001",
            event_type="incident.created",
            schema_version=1,
            payload={"incident_id": "inc_001"},
            occurred_at=NOW,
            trace_id="trc_001",
        ),
        attempts=attempts,
    )


class FakeDispatchStore:
    """记录发布器请求的状态流转。"""

    def __init__(self, claimed: list[ClaimedOutboxEvent]) -> None:
        self.claimed = claimed
        self.claim_calls: list[dict[str, object]] = []
        self.published: list[str] = []
        self.retried: list[tuple[str, datetime, str]] = []
        self.failed: list[tuple[str, str]] = []

    async def claim_batch(self, **kwargs):
        self.claim_calls.append(kwargs)
        return self.claimed

    async def mark_published(
        self,
        event_id: str,
        worker_id: str,
        published_at: datetime,
    ) -> None:
        self.published.append(event_id)

    async def mark_retry(
        self,
        event_id: str,
        worker_id: str,
        available_at: datetime,
        last_error: str,
    ) -> None:
        self.retried.append((event_id, available_at, last_error))

    async def mark_failed(
        self,
        event_id: str,
        worker_id: str,
        last_error: str,
    ) -> None:
        self.failed.append((event_id, last_error))


class FakePublisher:
    """按事件ID模拟成功、异常、超时或取消。"""

    def __init__(self, outcomes: dict[str, str]) -> None:
        self.outcomes = outcomes
        self.published_ids: list[str] = []

    async def publish(self, event: OutboxEvent) -> None:
        outcome = self.outcomes.get(event.event_id, "success")
        if outcome == "error":
            raise RuntimeError("broker unavailable")
        if outcome == "tab_error":
            raise RuntimeError("broker\tunavailable")
        if outcome == "del_error":
            raise RuntimeError("broker\x7funavailable")
        if outcome == "secret_error":
            raise RuntimeError("broker password=hunter2 token=secret-token")
        if outcome == "timeout":
            await asyncio.sleep(1)
        if outcome == "cancel":
            raise asyncio.CancelledError
        self.published_ids.append(event.event_id)


def test_dispatcher_config_rejects_lease_shorter_than_batch_runtime() -> None:
    with pytest.raises(AppValidationError, match="lease_duration"):
        OutboxDispatcherConfig(
            batch_size=2,
            publish_timeout=timedelta(seconds=5),
            lease_duration=timedelta(seconds=10),
        )


@pytest.mark.parametrize(
    "worker_id",
    [" worker_001", "worker\nforged", "w" * 129],
)
def test_invalid_worker_id_is_rejected(worker_id: str) -> None:
    """Outbox租约所有者不能携带日志污染字符或超长身份。"""
    with pytest.raises(AppValidationError, match="worker_id"):
        OutboxDispatcher(
            store=FakeDispatchStore([]),
            publisher=FakePublisher({}),
            worker_id=worker_id,
            clock=lambda: NOW,
        )


async def test_successful_event_is_marked_published() -> None:
    store = FakeDispatchStore([build_claimed("evt_001")])
    publisher = FakePublisher({})
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=publisher,
        worker_id="worker_001",
        config=OutboxDispatcherConfig(
            batch_size=1,
            publish_timeout=timedelta(seconds=1),
            lease_duration=timedelta(seconds=2),
        ),
        clock=lambda: NOW,
    )

    result = await dispatcher.run_once()

    assert result.published == 1
    assert result.retried == 0
    assert store.published == ["evt_001"]
    assert publisher.published_ids == ["evt_001"]


async def test_retryable_failure_uses_bounded_deterministic_backoff() -> None:
    store = FakeDispatchStore([build_claimed("evt_001", attempts=2)])
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=FakePublisher({"evt_001": "error"}),
        worker_id="worker_001",
        config=OutboxDispatcherConfig(
            batch_size=1,
            max_attempts=5,
            publish_timeout=timedelta(seconds=1),
            lease_duration=timedelta(seconds=2),
            base_backoff=timedelta(seconds=10),
            max_backoff=timedelta(seconds=60),
        ),
        clock=lambda: NOW,
    )

    result = await dispatcher.run_once()

    assert result.retried == 1
    event_id, available_at, error = store.retried[0]
    assert event_id == "evt_001"
    assert NOW + timedelta(seconds=10) <= available_at
    assert available_at <= NOW + timedelta(seconds=20)
    assert error == "RuntimeError: broker unavailable"


async def test_last_attempt_is_marked_failed() -> None:
    store = FakeDispatchStore([build_claimed("evt_001", attempts=5)])
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=FakePublisher({"evt_001": "error"}),
        worker_id="worker_001",
        config=OutboxDispatcherConfig(
            batch_size=1,
            max_attempts=5,
            publish_timeout=timedelta(seconds=1),
            lease_duration=timedelta(seconds=2),
        ),
        clock=lambda: NOW,
    )

    result = await dispatcher.run_once()

    assert result.failed == 1
    assert store.failed == [("evt_001", "RuntimeError: broker unavailable")]
    assert store.retried == []


async def test_dispatcher_redacts_error_before_retry_or_failure_state() -> None:
    """Outbox持久化错误摘要前必须脱敏，避免失败表成为凭证留存点。"""
    store = FakeDispatchStore(
        [
            build_claimed("evt_retry", attempts=1),
            build_claimed("evt_failed", attempts=5),
        ]
    )
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=FakePublisher(
            {
                "evt_retry": "secret_error",
                "evt_failed": "secret_error",
            }
        ),
        worker_id="worker_001",
        config=OutboxDispatcherConfig(
            batch_size=2,
            max_attempts=5,
            publish_timeout=timedelta(seconds=1),
            lease_duration=timedelta(seconds=3),
        ),
        clock=lambda: NOW,
    )

    result = await dispatcher.run_once()

    assert result.retried == 1
    assert result.failed == 1
    retry_error = store.retried[0][2]
    failed_error = store.failed[0][1]
    assert retry_error == ("RuntimeError: broker password=[REDACTED] token=[REDACTED]")
    assert failed_error == retry_error
    assert "hunter2" not in retry_error
    assert "secret-token" not in failed_error


@pytest.mark.parametrize("outcome", ["tab_error", "del_error"])
async def test_dispatcher_removes_control_characters_from_error_state(
    outcome: str,
) -> None:
    """异常摘要进入 Outbox 状态前要压成无控制字符的单行文本。"""
    store = FakeDispatchStore([build_claimed("evt_retry", attempts=1)])
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=FakePublisher({"evt_retry": outcome}),
        worker_id="worker_001",
        config=OutboxDispatcherConfig(
            batch_size=1,
            max_attempts=5,
            publish_timeout=timedelta(seconds=1),
            lease_duration=timedelta(seconds=2),
        ),
        clock=lambda: NOW,
    )

    result = await dispatcher.run_once()

    assert result.retried == 1
    assert store.retried[0][2] == "RuntimeError: broker unavailable"


async def test_publish_timeout_is_retried() -> None:
    store = FakeDispatchStore([build_claimed("evt_001")])
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=FakePublisher({"evt_001": "timeout"}),
        worker_id="worker_001",
        config=OutboxDispatcherConfig(
            batch_size=1,
            publish_timeout=timedelta(milliseconds=10),
            lease_duration=timedelta(seconds=1),
        ),
        clock=lambda: NOW,
    )

    result = await dispatcher.run_once()

    assert result.retried == 1
    assert store.retried[0][2] == "TimeoutError"


async def test_cancellation_is_not_converted_to_retry() -> None:
    store = FakeDispatchStore([build_claimed("evt_001")])
    dispatcher = OutboxDispatcher(
        store=store,
        publisher=FakePublisher({"evt_001": "cancel"}),
        worker_id="worker_001",
        config=OutboxDispatcherConfig(
            batch_size=1,
            publish_timeout=timedelta(seconds=1),
            lease_duration=timedelta(seconds=2),
        ),
        clock=lambda: NOW,
    )

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.run_once()

    assert store.retried == []
    assert store.failed == []
