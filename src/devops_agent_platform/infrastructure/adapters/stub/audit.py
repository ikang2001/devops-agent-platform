from typing import Any

from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton


class StubAuditLog:
    """不做持久化的审计日志占位实现。"""

    async def record(
        self,
        action: str,
        actor_id: str | None,
        resource_id: str | None,
        details: dict[str, Any],
        trace_id: str,
    ) -> None:
        raise NotImplementedInSkeleton(
            "StubAuditLog is unavailable in skeleton mode"
        )
