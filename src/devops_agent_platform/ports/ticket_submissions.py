from typing import Protocol

from devops_agent_platform.domain.models.ticket_submission import TicketSubmission


class TicketSubmissionRepositoryPort(Protocol):
    """当前事务内读写外部工单提交请求的端口。"""

    async def save(self, submission: TicketSubmission) -> None:
        """保存本地提交请求，不负责提交事务。"""
        ...

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketSubmission | None:
        """按租户与幂等摘要恢复已提交请求。"""
        ...

    async def get_by_draft_and_target(
        self,
        tenant_id: str,
        ticket_draft_id: str,
        target_system: str,
    ) -> TicketSubmission | None:
        """按草稿和目标系统读取唯一提交请求。"""
        ...

    async def get_by_id(
        self,
        tenant_id: str,
        ticket_submission_id: str,
    ) -> TicketSubmission | None:
        """按租户和提交请求标识读取记录。"""
        ...

    async def list_by_workflow(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        limit: int = 50,
    ) -> list[TicketSubmission]:
        """按工作流读取有限提交状态列表，禁止无界查询。"""
        ...

    async def get_by_result_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketSubmission | None:
        """按结果回填幂等摘要恢复已记录终态。"""
        ...

    async def apply_result(
        self,
        submission: TicketSubmission,
        expected_version: int,
    ) -> None:
        """按状态和版本条件原子写入外部提交结果。"""
        ...
