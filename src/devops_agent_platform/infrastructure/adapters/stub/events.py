from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton


class StubEventPublisher:
    """不产生副作用的事件发布占位实现。"""

    async def publish(
        self,
        event: OutboxEvent,
    ) -> None:
        raise NotImplementedInSkeleton(
            "StubEventPublisher is unavailable in skeleton mode"
        )
