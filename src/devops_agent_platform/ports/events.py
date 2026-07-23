from typing import Protocol

from devops_agent_platform.application.events import OutboxEvent


class EventPublisherPort(Protocol):
    """领域事件或应用事件发布端口。"""

    async def publish(
        self,
        event: OutboxEvent,
    ) -> None:
        """发布完整事件契约；消费者使用 event_id 实现幂等。"""
        ...
