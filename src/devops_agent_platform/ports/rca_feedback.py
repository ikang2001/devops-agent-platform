from typing import Protocol

from devops_agent_platform.domain.models.rca_feedback import RCAFeedback


class RCAFeedbackRepositoryPort(Protocol):
    """RCA 人工反馈的事务内持久化端口。"""

    async def save(self, feedback: RCAFeedback) -> None:
        """保存一条不可变反馈。"""
        ...

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> RCAFeedback | None:
        """按租户幂等摘要读取反馈。"""
        ...

    async def get_by_id(
        self,
        tenant_id: str,
        feedback_id: str,
    ) -> RCAFeedback | None:
        """按租户和反馈 ID 精确读取一条反馈。"""
        ...

    async def list_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
        limit: int,
    ) -> list[RCAFeedback]:
        """按创建时间倒序读取有限反馈历史。"""
        ...
