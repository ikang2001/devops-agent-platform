from typing import Any

from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton


class StubEventPublisher:
    """不产生副作用的事件发布占位实现。"""

    async def publish(
        self,
        event_name: str,
        payload: dict[str, Any],
        trace_id: str,
    ) -> None:
        raise NotImplementedInSkeleton("StubEventPublisher is a Step 3 placeholder")
