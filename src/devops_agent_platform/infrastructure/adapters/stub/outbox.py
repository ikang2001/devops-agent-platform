from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton


class StubOutboxRepository:
    """拒绝伪造事件已进入可靠投递链路的 Outbox 占位实现。"""

    async def add(self, event: OutboxEvent) -> None:
        raise NotImplementedInSkeleton(
            "StubOutboxRepository is unavailable in skeleton mode"
        )
