from typing import Any, Protocol


class EventPublisherPort(Protocol):
    """领域事件或应用事件发布端口。"""

    async def publish(
        self,
        event_name: str,
        payload: dict[str, Any],
        trace_id: str,
    ) -> None:
        """在用例确认安全后发布事件。"""
        ...
