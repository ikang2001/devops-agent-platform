import logging
from collections.abc import Callable
from types import TracebackType

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.ports.locks import IncidentCorrelationLockPort

from .alert_repository import SQLAlchemyAlertRepository
from .evidence_repository import SQLAlchemyEvidenceRepository
from .incident_lock import PostgreSQLIncidentCorrelationLock
from .incident_repository import SQLAlchemyIncidentRepository
from .outbox_repository import SQLAlchemyOutboxRepository
from .rca_feedback_repository import SQLAlchemyRCAFeedbackRepository
from .rca_report_repository import SQLAlchemyRCAReportRepository
from .remediation_repository import SQLAlchemyRemediationPlanRepository
from .ticket_draft_repository import SQLAlchemyTicketDraftRepository
from .ticket_submission_repository import SQLAlchemyTicketSubmissionRepository
from .tool_invocation_repository import SQLAlchemyToolInvocationRepository
from .workflow_run_repository import SQLAlchemyWorkflowRunRepository

logger = logging.getLogger(__name__)
CorrelationLockFactory = Callable[
    [AsyncSession],
    IncidentCorrelationLockPort,
]


class SQLAlchemyUnitOfWork:
    """使用 AsyncSession 实现的单用例事务边界。

    每个实例只能同时服务一个异步上下文，禁止跨请求和并发任务共享。只有调用
    ``commit`` 才会提交；其余退出路径均回滚未完成事务，并最终关闭 Session。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        correlation_lock_factory: CorrelationLockFactory | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._correlation_lock_factory = (
            correlation_lock_factory or PostgreSQLIncidentCorrelationLock
        )
        self._session: AsyncSession | None = None
        self._alerts: SQLAlchemyAlertRepository | None = None
        self._incidents: SQLAlchemyIncidentRepository | None = None
        self._incident_correlation_lock: IncidentCorrelationLockPort | None = None
        self._outbox: SQLAlchemyOutboxRepository | None = None
        self._workflow_runs: SQLAlchemyWorkflowRunRepository | None = None
        self._evidence: SQLAlchemyEvidenceRepository | None = None
        self._tool_invocations: SQLAlchemyToolInvocationRepository | None = None
        self._rca_reports: SQLAlchemyRCAReportRepository | None = None
        self._rca_feedback: SQLAlchemyRCAFeedbackRepository | None = None
        self._remediation_plans: SQLAlchemyRemediationPlanRepository | None = None
        self._ticket_drafts: SQLAlchemyTicketDraftRepository | None = None
        self._ticket_submissions: SQLAlchemyTicketSubmissionRepository | None = None
        self._completed = False

    @property
    def alerts(self) -> SQLAlchemyAlertRepository:
        """返回当前事务共享的告警仓储。

        异常：
            RuntimeError: 在事务上下文之外访问仓储。
        """
        self._require_active_session()
        if self._alerts is None:
            raise RuntimeError("Unit of work repositories are not initialized")
        return self._alerts

    @property
    def incidents(self) -> SQLAlchemyIncidentRepository:
        """返回当前事务共享的事故仓储。"""
        self._require_active_session()
        if self._incidents is None:
            raise RuntimeError("Unit of work repositories are not initialized")
        return self._incidents

    @property
    def incident_correlation_lock(self) -> IncidentCorrelationLockPort:
        """返回绑定当前 Session 的事故关联锁。"""
        self._require_active_session()
        if self._incident_correlation_lock is None:
            raise RuntimeError("Unit of work lock is not initialized")
        return self._incident_correlation_lock

    @property
    def outbox(self) -> SQLAlchemyOutboxRepository:
        """返回当前事务共享的 Outbox 仓储。"""
        self._require_active_session()
        if self._outbox is None:
            raise RuntimeError("Unit of work outbox is not initialized")
        return self._outbox

    @property
    def workflow_runs(self) -> SQLAlchemyWorkflowRunRepository:
        """返回与事故、Outbox共享事务的工作流运行仓储。"""
        self._require_active_session()
        if self._workflow_runs is None:
            raise RuntimeError("Unit of work workflow runs are not initialized")
        return self._workflow_runs

    @property
    def evidence(self) -> SQLAlchemyEvidenceRepository:
        """返回与工作流运行共享事务的证据仓储。"""
        self._require_active_session()
        if self._evidence is None:
            raise RuntimeError("Unit of work evidence is not initialized")
        return self._evidence

    @property
    def tool_invocations(self) -> SQLAlchemyToolInvocationRepository:
        """返回与工作流终态共享事务的工具调用审计仓储。"""
        self._require_active_session()
        if self._tool_invocations is None:
            raise RuntimeError("Unit of work tool invocations are not initialized")
        return self._tool_invocations

    @property
    def rca_reports(self) -> SQLAlchemyRCAReportRepository:
        """返回与工作流终态共享事务的 RCA 报告仓储。"""
        self._require_active_session()
        if self._rca_reports is None:
            raise RuntimeError("Unit of work RCA reports are not initialized")
        return self._rca_reports

    @property
    def rca_feedback(self) -> SQLAlchemyRCAFeedbackRepository:
        """返回与报告和 Outbox 共享事务的人工反馈仓储。"""
        self._require_active_session()
        if self._rca_feedback is None:
            raise RuntimeError("Unit of work RCA feedback is not initialized")
        return self._rca_feedback

    @property
    def remediation_plans(self) -> SQLAlchemyRemediationPlanRepository:
        """返回与报告和 Outbox 共享事务的受控修复计划仓储。"""
        self._require_active_session()
        if self._remediation_plans is None:
            raise RuntimeError("Unit of work remediation plans are not initialized")
        return self._remediation_plans

    @property
    def ticket_drafts(self) -> SQLAlchemyTicketDraftRepository:
        """返回与报告和 Outbox 共享事务的工单草稿仓储。"""
        self._require_active_session()
        if self._ticket_drafts is None:
            raise RuntimeError("Unit of work ticket drafts are not initialized")
        return self._ticket_drafts

    @property
    def ticket_submissions(self) -> SQLAlchemyTicketSubmissionRepository:
        """返回与草稿和 Outbox 共享事务的工单提交请求仓储。"""
        self._require_active_session()
        if self._ticket_submissions is None:
            raise RuntimeError("Unit of work ticket submissions are not initialized")
        return self._ticket_submissions

    async def __aenter__(self) -> "SQLAlchemyUnitOfWork":
        """创建请求级 Session，并让本事务内仓储共享同一连接上下文。"""
        if self._session is not None:
            raise RuntimeError("Unit of work is already active")

        self._session = self._session_factory()
        self._alerts = SQLAlchemyAlertRepository(self._session)
        self._incidents = SQLAlchemyIncidentRepository(self._session)
        self._outbox = SQLAlchemyOutboxRepository(self._session)
        self._workflow_runs = SQLAlchemyWorkflowRunRepository(self._session)
        self._evidence = SQLAlchemyEvidenceRepository(self._session)
        self._tool_invocations = SQLAlchemyToolInvocationRepository(self._session)
        self._rca_reports = SQLAlchemyRCAReportRepository(self._session)
        self._rca_feedback = SQLAlchemyRCAFeedbackRepository(self._session)
        self._remediation_plans = SQLAlchemyRemediationPlanRepository(self._session)
        self._ticket_drafts = SQLAlchemyTicketDraftRepository(self._session)
        self._ticket_submissions = SQLAlchemyTicketSubmissionRepository(self._session)
        self._incident_correlation_lock = self._correlation_lock_factory(self._session)
        self._completed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """回滚遗留事务并关闭 Session，始终让原始业务异常继续向上传播。"""
        session = self._require_active_session()
        cleanup_error: PersistenceError | None = None

        try:
            if session.in_transaction():
                await session.rollback()
        except SQLAlchemyError as exc:
            cleanup_error = PersistenceError("Could not rollback database transaction")
            cleanup_error.__cause__ = exc
        finally:
            try:
                await session.close()
            except SQLAlchemyError as exc:
                if cleanup_error is None:
                    cleanup_error = PersistenceError("Could not close database session")
                    cleanup_error.__cause__ = exc
            self._clear_state()

        if exc_value is not None:
            if cleanup_error is not None:
                logger.error("事务清理失败，保留原始业务异常")
            return False

        if cleanup_error is not None:
            raise cleanup_error
        return False

    async def commit(self) -> None:
        """提交当前事务，并将数据库异常转换为稳定应用异常。

        异常：
            RuntimeError: 上下文未激活，或事务已经显式结束。
            ConflictError: 延迟到提交阶段才触发的数据完整性冲突。
            PersistenceError: 提交或失败回滚过程中出现数据库故障。
        """
        session = self._require_open_transaction()
        try:
            await session.commit()
        except IntegrityError as exc:
            await self._rollback_after_commit_failure(session)
            raise ConflictError("Transaction persistence conflict") from exc
        except SQLAlchemyError as exc:
            await self._rollback_after_commit_failure(session)
            raise PersistenceError("Could not commit database transaction") from exc

        self._completed = True

    async def rollback(self) -> None:
        """显式回滚当前事务；重复结束同一个事务会被拒绝。"""
        session = self._require_open_transaction()
        try:
            await session.rollback()
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not rollback database transaction") from exc

        self._completed = True

    def _require_active_session(self) -> AsyncSession:
        """返回活动 Session，阻止在生命周期之外操作数据库。"""
        if self._session is None:
            raise RuntimeError("Unit of work is not active")
        return self._session

    def _require_open_transaction(self) -> AsyncSession:
        """返回尚未显式结束的 Session，避免重复提交或回滚。"""
        session = self._require_active_session()
        if self._completed:
            raise RuntimeError("Unit of work transaction is already completed")
        return session

    async def _rollback_after_commit_failure(self, session: AsyncSession) -> None:
        """提交失败后恢复 Session；回滚失败时优先报告未知事务状态。"""
        try:
            await session.rollback()
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not rollback failed database transaction"
            ) from exc
        finally:
            self._completed = True

    def _clear_state(self) -> None:
        """清除请求级对象引用，防止 Session 被意外复用。"""
        self._alerts = None
        self._incidents = None
        self._incident_correlation_lock = None
        self._outbox = None
        self._workflow_runs = None
        self._evidence = None
        self._tool_invocations = None
        self._rca_reports = None
        self._rca_feedback = None
        self._remediation_plans = None
        self._ticket_drafts = None
        self._ticket_submissions = None
        self._session = None
        self._completed = False
