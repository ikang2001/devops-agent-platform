from typing import Any, Protocol


class AuditLogPort(Protocol):
    """安全审计与运维审计日志写入端口。"""

    async def record(
        self,
        action: str,
        actor_id: str | None,
        resource_id: str | None,
        details: dict[str, Any],
        trace_id: str,
    ) -> None:
        """记录一条携带 trace 上下文的审计事件。"""
        ...
