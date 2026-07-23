from datetime import datetime
from typing import Protocol


class AuditRetentionStorePort(Protocol):
    """RCA 审计数据批量清理端口。"""

    async def purge_batch(
        self,
        *,
        cutoff: datetime,
        purged_at: datetime,
        limit: int,
    ) -> int:
        """原子清理一批终态工作流的 Evidence 与工具调用记录。"""
        ...
