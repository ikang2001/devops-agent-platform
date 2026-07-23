# 在你的 DevOps 智能排障 Agent 项目里，应用层可能有这种用例：
# 接收告警
# ↓
# 保存告警
# ↓
# 创建事件
# ↓
# 记录日志
# ↓
# 提交数据库事务
# 这些操作必须保证 要么全部成功，要么全部失败。
# 比如：
# 保存告警成功了
# 创建事件失败了
# 那就不能只保存一半，否则数据库数据就乱了。

# 所以需要一个东西统一管理事务：

# 开始事务
# ↓
# 执行多个仓储操作
# ↓
# 全部成功：commit
# 中途失败：rollback
# ↓
# 关闭资源

# 这个东西就是 UnitOfWorkPort。

# 这段代码的作用是：

# 定义一个事务边界接口，让应用层通过 uow.alerts 操作仓储，并通过
# commit/rollback 统一控制数据库事务，避免应用层直接依赖 SQLAlchemy，
# 实现业务逻辑和数据库实现解耦。

# 你可以把它理解成：

# UnitOfWorkPort = 应用层操作数据库时的“事务总管”

# 仓储负责“干活”：

# 保存告警、查询告警

# UoW 负责“管事务”：

# 开始、提交、失败回滚、关闭资源

from types import TracebackType
from typing import Protocol, Self

from devops_agent_platform.ports.evidence import EvidenceRepositoryPort
from devops_agent_platform.ports.locks import IncidentCorrelationLockPort
from devops_agent_platform.ports.outbox import OutboxRepositoryPort
from devops_agent_platform.ports.rca_report import RCAReportRepositoryPort
from devops_agent_platform.ports.repositories import (
    AlertRepositoryPort,
    IncidentRepositoryPort,
)
from devops_agent_platform.ports.ticket_drafts import (
    TicketDraftRepositoryPort,
)
from devops_agent_platform.ports.ticket_submissions import (
    TicketSubmissionRepositoryPort,
)
from devops_agent_platform.ports.tool_invocation import (
    ToolInvocationRepositoryPort,
)
from devops_agent_platform.ports.workflow import WorkflowRunRepositoryPort


class UnitOfWorkPort(Protocol):
    """应用用例依赖的事务边界端口。

    应用层通过该协议组合仓储并控制原子提交，不需要感知 SQLAlchemy Session、
    数据库连接池或具体事务实现。
    """

    @property
    def alerts(self) -> AlertRepositoryPort:
        """返回当前事务作用域内的告警仓储。"""
        ...

    @property
    def incidents(self) -> IncidentRepositoryPort:
        """返回当前事务作用域内的事故仓储。"""
        ...

    @property
    def incident_correlation_lock(self) -> IncidentCorrelationLockPort:
        """返回与当前事务绑定的事故关联锁。"""
        ...

    @property
    def outbox(self) -> OutboxRepositoryPort:
        """返回与业务仓储共享当前事务的 Outbox 仓储。"""
        ...

    @property
    def workflow_runs(self) -> WorkflowRunRepositoryPort:
        """返回当前事务作用域内的WorkflowRun仓储。"""
        ...

    @property
    def evidence(self) -> EvidenceRepositoryPort:
        """返回当前事务作用域内的 RCA 证据仓储。"""
        ...

    @property
    def tool_invocations(self) -> ToolInvocationRepositoryPort:
        """返回当前事务作用域内的工具调用审计仓储。"""
        ...

    @property
    def rca_reports(self) -> RCAReportRepositoryPort:
        """返回当前事务作用域内的 RCA 报告仓储。"""
        ...

    @property
    def ticket_drafts(self) -> TicketDraftRepositoryPort:
        """返回当前事务作用域内的本地工单草稿仓储。"""
        ...

    @property
    def ticket_submissions(self) -> TicketSubmissionRepositoryPort:
        """返回当前事务作用域内的外部工单提交请求仓储。"""
        ...

    async def __aenter__(self) -> Self:
        """进入事务作用域并准备仓储资源。"""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """退出事务作用域，必要时回滚并释放资源。"""
        ...

    async def commit(self) -> None:
        """原子提交当前用例产生的全部数据库变更。"""
        ...

    async def rollback(self) -> None:
        """显式回滚当前用例产生的全部数据库变更。"""
        ...
