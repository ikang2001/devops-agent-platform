from typing import Protocol

from devops_agent_platform.domain.models.ticket_draft import TicketDraft


class TicketDraftRepositoryPort(Protocol):
    """当前事务内读写本地工单草稿的端口。"""

    async def save(self, draft: TicketDraft) -> None:
        """写入不可变草稿，不负责提交事务。"""
        ...

    async def get_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> TicketDraft | None:
        """按租户与工作流读取唯一草稿。"""
        ...

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketDraft | None:
        """按租户与幂等摘要恢复已提交结果。"""
        ...

    async def get_by_decision_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketDraft | None:
        """按审批幂等摘要恢复已提交决策。"""
        ...

    async def apply_decision(
        self,
        draft: TicketDraft,
        expected_version: int,
    ) -> None:
        """按状态和版本条件原子写入人工确认结果。"""
        ...
