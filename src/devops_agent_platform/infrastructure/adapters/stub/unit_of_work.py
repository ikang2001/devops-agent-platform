from types import TracebackType

from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton
from devops_agent_platform.infrastructure.adapters.stub.locks import (
    StubIncidentCorrelationLock,
)
from devops_agent_platform.infrastructure.adapters.stub.outbox import (
    StubOutboxRepository,
)
from devops_agent_platform.infrastructure.adapters.stub.repositories import (
    StubAlertRepository,
    StubChangeEventRepository,
    StubIncidentRepository,
    StubTicketSubmissionRepository,
    StubWorkflowRunRepository,
)


class StubUnitOfWork:
    """仅用于尚未装配真实数据库生命周期的 HTTP 骨架。"""

    def __init__(self) -> None:
        self.alerts = StubAlertRepository()
        self.change_events = StubChangeEventRepository()
        self.incidents = StubIncidentRepository()
        self.workflow_runs = StubWorkflowRunRepository()
        self.ticket_submissions = StubTicketSubmissionRepository()
        self.incident_correlation_lock = StubIncidentCorrelationLock()
        self.outbox = StubOutboxRepository()

    async def __aenter__(self) -> "StubUnitOfWork":
        """返回占位事务上下文，不创建数据库连接。"""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """不吞掉占位适配器抛出的异常。"""
        return False

    async def commit(self) -> None:
        """拒绝伪造事务提交成功。"""
        raise NotImplementedInSkeleton("StubUnitOfWork cannot commit")

    async def rollback(self) -> None:
        """拒绝伪造事务回滚成功。"""
        raise NotImplementedInSkeleton("StubUnitOfWork cannot rollback")
