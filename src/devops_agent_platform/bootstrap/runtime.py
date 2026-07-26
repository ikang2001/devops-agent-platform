import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from socket import gethostname
from time import monotonic
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from devops_agent_platform.application.services import (
    ticket_submission_consumer_runner as ticket_consumer_runner,
)
from devops_agent_platform.application.services.alert_service import (
    AlertApplicationService,
)
from devops_agent_platform.application.services.audit_retention_service import (
    AuditRetentionConfig,
    AuditRetentionService,
)
from devops_agent_platform.application.services.audit_retention_worker import (
    AuditRetentionWorkerConfig,
    AuditRetentionWorkerHealth,
    AuditRetentionWorkerRunner,
)
from devops_agent_platform.application.services.incident_query_service import (
    IncidentQueryService,
)
from devops_agent_platform.application.services.incident_resolution_service import (
    IncidentResolutionService,
)
from devops_agent_platform.application.services.notification_service import (
    NotificationApplicationService,
)
from devops_agent_platform.application.services.outbox_backlog import (
    OutboxBacklogMonitor,
    OutboxBacklogMonitorConfig,
)
from devops_agent_platform.application.services.outbox_dispatcher import (
    OutboxDispatcher,
    OutboxDispatcherConfig,
)
from devops_agent_platform.application.services.outbox_worker import (
    OutboxWorkerHealth,
    OutboxWorkerRunner,
)
from devops_agent_platform.application.services.rca_cancellation_service import (
    RCACancellationService,
)
from devops_agent_platform.application.services.rca_consumer_runner import (
    RCAConsumerHealth,
    RCAConsumerRunner,
)
from devops_agent_platform.application.services.rca_feedback_service import (
    RCAFeedbackApplicationService,
)
from devops_agent_platform.application.services.rca_query_service import (
    RCAExecutionQueryService,
)
from devops_agent_platform.application.services.rca_service import (
    RCAApplicationService,
)
from devops_agent_platform.application.services.remediation_reclaim_worker import (
    RemediationReclaimWorkerConfig,
    RemediationReclaimWorkerHealth,
    RemediationReclaimWorkerRunner,
)
from devops_agent_platform.application.services.remediation_service import (
    RemediationApplicationService,
)
from devops_agent_platform.application.services.runbook_admin_service import (
    RunbookAdminService,
)
from devops_agent_platform.application.services.ticket_draft_service import (
    TicketDraftApplicationService,
)
from devops_agent_platform.application.services.ticket_submission_service import (
    TicketSubmissionApplicationService,
)
from devops_agent_platform.application.services.tool_permission_admin_service import (
    ToolPermissionAdminService,
)
from devops_agent_platform.bootstrap.rca_runtime import (
    AsyncCloseable,
    build_rca_consumer_runtime,
)
from devops_agent_platform.bootstrap.readiness import (
    ComponentReadiness,
    ReadinessSnapshot,
)
from devops_agent_platform.bootstrap.ticket_submission_runtime import (
    build_ticket_submission_consumer_runtime,
)
from devops_agent_platform.bootstrap.worker_identity import derive_suffixed_id
from devops_agent_platform.domain.policies.incident_creation import (
    IncidentCreationPolicy,
)
from devops_agent_platform.domain.policies.remediation import (
    RemediationExecutionPolicy,
)
from devops_agent_platform.infrastructure.adapters.kafka import (
    KafkaEventPublisher,
    KafkaPublisherConfig,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyAuditRetentionStore,
    SQLAlchemyOutboxDispatchStore,
    SQLAlchemyOutboxMetricsReader,
    SQLAlchemyRunbookAdminStore,
    SQLAlchemyToolPermissionAdminStore,
    SQLAlchemyUnitOfWork,
)
from devops_agent_platform.infrastructure.auth import (
    DemoAdministratorAuthenticator,
    DemoAdministratorAuthenticatorConfig,
    OIDCAdministratorAuthenticator,
    OIDCAuthenticatorConfig,
)
from devops_agent_platform.infrastructure.config.settings import Settings
from devops_agent_platform.infrastructure.database.session import (
    create_engine,
    create_session_factory,
)
from devops_agent_platform.infrastructure.identifiers import UUIDIdentifierGenerator
from devops_agent_platform.infrastructure.notifications import (
    NotificationWebhookConfig,
    RoutingNotificationGateway,
    WebhookNotificationGateway,
)
from devops_agent_platform.infrastructure.remediation import (
    HttpRemediationExecutor,
    HttpRemediationExecutorConfig,
    JsonRemediationActionCatalog,
)
from devops_agent_platform.infrastructure.ticketing import (
    HttpJsonTicketingGateway,
    HttpJsonTicketingGatewayConfig,
    JiraTicketingGateway,
    JiraTicketingGatewayConfig,
    RoutingTicketingGateway,
    ServiceNowTicketingGateway,
    ServiceNowTicketingGatewayConfig,
)
from devops_agent_platform.ports.authentication import (
    AdministratorAuthenticatorPort,
)
from devops_agent_platform.ports.notifications import (
    NotificationGatewayPort,
)
from devops_agent_platform.ports.rca_report import (
    LLMReportGenerationObserverPort,
)
from devops_agent_platform.ports.remediation import (
    RemediationActionCatalogPort,
    RemediationExecutorPort,
)
from devops_agent_platform.ports.ticketing import (
    TicketingGatewayObserverPort,
    TicketingGatewayPort,
)

logger = logging.getLogger(__name__)
MonotonicClock = Callable[[], float]
CleanupFailure = tuple[str, BaseException]
TicketSubmissionConsumerHealth = ticket_consumer_runner.TicketSubmissionConsumerHealth
TicketSubmissionConsumerRunner = ticket_consumer_runner.TicketSubmissionConsumerRunner


class PublisherLifecycle(Protocol):
    """Runtime管理的消息发布器生命周期。"""

    async def start(self) -> None:
        """启动发布器。"""
        ...

    async def close(self) -> None:
        """关闭发布器。"""
        ...


class WorkerLifecycle(Protocol):
    """Runtime管理的后台Worker生命周期。"""

    async def run(self) -> None:
        """运行Worker循环。"""
        ...

    def request_stop(self) -> None:
        """请求协作式停止。"""
        ...


class AuthenticatorLifecycle(AdministratorAuthenticatorPort, Protocol):
    """Runtime托管的管理员认证器生命周期。"""

    async def close(self) -> None:
        """释放认证器持有的网络资源。"""
        ...


@dataclass
class ApplicationRuntime:
    """管理应用级数据库、消息发布器和后台Worker资源。"""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    alert_service: AlertApplicationService
    rca_service: RCAApplicationService
    shutdown_timeout_seconds: float
    rca_query_service: RCAExecutionQueryService | None = None
    rca_feedback_service: RCAFeedbackApplicationService | None = None
    remediation_service: RemediationApplicationService | None = None
    rca_cancellation_service: RCACancellationService | None = None
    incident_query_service: IncidentQueryService | None = None
    incident_resolution_service: IncidentResolutionService | None = None
    permission_admin_service: ToolPermissionAdminService | None = None
    runbook_admin_service: RunbookAdminService | None = None
    ticket_draft_service: TicketDraftApplicationService | None = None
    ticket_submission_service: TicketSubmissionApplicationService | None = None
    notification_service: NotificationApplicationService | None = None
    admin_authenticator: OIDCAdministratorAuthenticator | None = None
    readiness_database_timeout_seconds: float = 1.0
    readiness_cache_ttl_seconds: float = 2.0
    rca_consumer_shutdown_timeout_seconds: float = 30.0
    audit_retention_shutdown_timeout_seconds: float = 30.0
    remediation_reclaim_shutdown_timeout_seconds: float = 30.0
    ticket_submission_consumer_shutdown_timeout_seconds: float = 30.0
    publisher: PublisherLifecycle | None = None
    worker: WorkerLifecycle | None = None
    rca_consumer_worker: WorkerLifecycle | None = None
    audit_retention_worker: WorkerLifecycle | None = None
    remediation_reclaim_worker: WorkerLifecycle | None = None
    ticket_submission_consumer_worker: WorkerLifecycle | None = None
    outbox_backlog_monitor: OutboxBacklogMonitor | None = None
    managed_resources: tuple[AsyncCloseable, ...] = ()
    readiness_clock: MonotonicClock = monotonic

    def __post_init__(self) -> None:
        if self.readiness_database_timeout_seconds <= 0:
            raise ValueError("readiness_database_timeout_seconds must be positive")
        if self.readiness_cache_ttl_seconds < 0:
            raise ValueError("readiness_cache_ttl_seconds must not be negative")
        if self.rca_consumer_shutdown_timeout_seconds <= 0:
            raise ValueError("rca_consumer_shutdown_timeout_seconds must be positive")
        if self.audit_retention_shutdown_timeout_seconds <= 0:
            raise ValueError(
                "audit_retention_shutdown_timeout_seconds must be positive"
            )
        if self.remediation_reclaim_shutdown_timeout_seconds <= 0:
            raise ValueError(
                "remediation_reclaim_shutdown_timeout_seconds must be positive"
            )
        if self.ticket_submission_consumer_shutdown_timeout_seconds <= 0:
            raise ValueError(
                "ticket_submission_consumer_shutdown_timeout_seconds must be positive"
            )
        if not isinstance(self.managed_resources, tuple) or not all(
            callable(getattr(resource, "close", None))
            for resource in self.managed_resources
        ):
            raise ValueError("managed_resources must contain async closeable resources")
        self._start_attempted = False
        self._started = False
        self._closing = False
        self._closed = False
        self._worker_task: asyncio.Task[None] | None = None
        self._worker_failure: str | None = None
        self._rca_consumer_task: asyncio.Task[None] | None = None
        self._rca_consumer_failure: str | None = None
        self._audit_retention_task: asyncio.Task[None] | None = None
        self._audit_retention_failure: str | None = None
        self._remediation_reclaim_task: asyncio.Task[None] | None = None
        self._remediation_reclaim_failure: str | None = None
        self._ticket_submission_consumer_task: asyncio.Task[None] | None = None
        self._ticket_submission_consumer_failure: str | None = None
        self._database_ready_cache: bool | None = None
        self._database_checked_at = float("-inf")
        self._readiness_lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        """返回Runtime是否已完成启动且尚未关闭。"""
        return self._started and not self._closed

    @property
    def worker_health(self) -> OutboxWorkerHealth | None:
        """返回Worker不可变健康快照；未启用或替身不支持时返回None。"""
        health = getattr(self.worker, "health", None)
        return health if isinstance(health, OutboxWorkerHealth) else None

    @property
    def rca_consumer_health(self) -> RCAConsumerHealth | None:
        """返回RCA Consumer健康快照；未启用或替身不支持时返回None。"""
        health = getattr(self.rca_consumer_worker, "health", None)
        return health if isinstance(health, RCAConsumerHealth) else None

    @property
    def audit_retention_health(self) -> AuditRetentionWorkerHealth | None:
        """返回审计清理 Worker 健康快照。"""
        health = getattr(self.audit_retention_worker, "health", None)
        return health if isinstance(health, AuditRetentionWorkerHealth) else None

    @property
    def remediation_reclaim_health(
        self,
    ) -> RemediationReclaimWorkerHealth | None:
        """返回修复租约回收 Worker 健康快照。"""
        health = getattr(self.remediation_reclaim_worker, "health", None)
        return health if isinstance(health, RemediationReclaimWorkerHealth) else None

    @property
    def ticket_submission_consumer_health(
        self,
    ) -> TicketSubmissionConsumerHealth | None:
        """返回外部工单提交消费 Worker 健康快照。"""
        health = getattr(
            self.ticket_submission_consumer_worker,
            "health",
            None,
        )
        return health if isinstance(health, TicketSubmissionConsumerHealth) else None

    async def start(self) -> None:
        """按数据库、Kafka、Worker顺序启动全部资源。"""
        if self._start_attempted:
            raise RuntimeError("Application runtime cannot be started twice")
        self._start_attempted = True

        try:
            await self._check_database()
            self._database_ready_cache = True
            self._database_checked_at = self.readiness_clock()
            if self.publisher is not None:
                await self.publisher.start()
            if self.worker is not None:
                self._worker_task = asyncio.create_task(
                    self.worker.run(),
                    name="outbox-worker",
                )
                self._worker_task.add_done_callback(
                    partial(
                        self._on_worker_done,
                        worker_name="outbox",
                    )
                )
            if self.rca_consumer_worker is not None:
                self._rca_consumer_task = asyncio.create_task(
                    self.rca_consumer_worker.run(),
                    name="rca-consumer-worker",
                )
                self._rca_consumer_task.add_done_callback(
                    partial(
                        self._on_worker_done,
                        worker_name="rca_consumer",
                    )
                )
            if self.audit_retention_worker is not None:
                self._audit_retention_task = asyncio.create_task(
                    self.audit_retention_worker.run(),
                    name="audit-retention-worker",
                )
                self._audit_retention_task.add_done_callback(
                    partial(
                        self._on_worker_done,
                        worker_name="audit_retention",
                    )
                )
            if self.remediation_reclaim_worker is not None:
                self._remediation_reclaim_task = asyncio.create_task(
                    self.remediation_reclaim_worker.run(),
                    name="remediation-reclaim-worker",
                )
                self._remediation_reclaim_task.add_done_callback(
                    partial(
                        self._on_worker_done,
                        worker_name="remediation_reclaim",
                    )
                )
            if self.ticket_submission_consumer_worker is not None:
                self._ticket_submission_consumer_task = asyncio.create_task(
                    self.ticket_submission_consumer_worker.run(),
                    name="ticket-submission-consumer-worker",
                )
                self._ticket_submission_consumer_task.add_done_callback(
                    partial(
                        self._on_worker_done,
                        worker_name="ticket_submission_consumer",
                    )
                )
            if (
                self._worker_task is not None
                or self._rca_consumer_task is not None
                or self._audit_retention_task is not None
                or self._remediation_reclaim_task is not None
                or self._ticket_submission_consumer_task is not None
            ):
                await asyncio.sleep(0)
            if self._worker_task is not None and self._worker_task.done():
                await self._worker_task
            if self._rca_consumer_task is not None and self._rca_consumer_task.done():
                await self._rca_consumer_task
            if (
                self._audit_retention_task is not None
                and self._audit_retention_task.done()
            ):
                await self._audit_retention_task
            if (
                self._remediation_reclaim_task is not None
                and self._remediation_reclaim_task.done()
            ):
                await self._remediation_reclaim_task
            if (
                self._ticket_submission_consumer_task is not None
                and self._ticket_submission_consumer_task.done()
            ):
                await self._ticket_submission_consumer_task
            self._started = True
        except BaseException:
            await self._close_resources(suppress_errors=True)
            raise

    async def close(self) -> None:
        """按Worker、Kafka、数据库顺序关闭资源，重复调用安全。"""
        if self._closed:
            return
        await self._close_resources(suppress_errors=False)

    async def check_readiness(self) -> ReadinessSnapshot:
        """聚合运行时、数据库和Worker任务状态。

        数据库是告警写入链路的硬依赖；Worker未启用属于合法部署形态，已启用但
        任务退出则代表后台投递链路失效。Kafka瞬时发布失败由Worker退避处理，
        不在这里直接摘除仍可持久化告警的API实例。
        """
        runtime_status = (
            ComponentReadiness.UP
            if self.started and not self._closing
            else ComponentReadiness.DOWN
        )
        if runtime_status is ComponentReadiness.UP:
            database_status = (
                ComponentReadiness.UP
                if await self._database_is_ready()
                else ComponentReadiness.DOWN
            )
        else:
            database_status = ComponentReadiness.UNKNOWN

        return ReadinessSnapshot(
            components=(
                ("runtime", runtime_status),
                ("database", database_status),
                (
                    "outbox_worker",
                    self._background_worker_readiness(
                        self.worker,
                        self._worker_task,
                        self._worker_failure,
                    ),
                ),
                (
                    "rca_consumer",
                    self._background_worker_readiness(
                        self.rca_consumer_worker,
                        self._rca_consumer_task,
                        self._rca_consumer_failure,
                    ),
                ),
                (
                    "audit_retention",
                    self._background_worker_readiness(
                        self.audit_retention_worker,
                        self._audit_retention_task,
                        self._audit_retention_failure,
                    ),
                ),
                (
                    "remediation_reclaim",
                    self._background_worker_readiness(
                        self.remediation_reclaim_worker,
                        self._remediation_reclaim_task,
                        self._remediation_reclaim_failure,
                    ),
                ),
                (
                    "ticket_submission_consumer",
                    self._background_worker_readiness(
                        self.ticket_submission_consumer_worker,
                        self._ticket_submission_consumer_task,
                        self._ticket_submission_consumer_failure,
                    ),
                ),
            )
        )

    async def _check_database(self) -> None:
        """启动阶段执行轻量查询，避免应用假健康。"""
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def _database_is_ready(self) -> bool:
        """带超时、短缓存和并发单飞地检查数据库连接。"""
        now = self.readiness_clock()
        if self._database_cache_is_fresh(now):
            return bool(self._database_ready_cache)

        async with self._readiness_lock:
            now = self.readiness_clock()
            if self._database_cache_is_fresh(now):
                return bool(self._database_ready_cache)
            try:
                await asyncio.wait_for(
                    self._check_database(),
                    timeout=self.readiness_database_timeout_seconds,
                )
                ready = True
            except Exception:
                ready = False
                logger.warning("数据库就绪检查失败")
            self._database_ready_cache = ready
            self._database_checked_at = self.readiness_clock()
            return ready

    def _database_cache_is_fresh(self, now: float) -> bool:
        """判断数据库探活缓存是否仍在允许复用的短窗口内。"""
        return (
            self._database_ready_cache is not None
            and now - self._database_checked_at < self.readiness_cache_ttl_seconds
        )

    @staticmethod
    def _background_worker_readiness(
        worker: WorkerLifecycle | None,
        task: asyncio.Task[None] | None,
        failure: str | None,
    ) -> ComponentReadiness:
        """判断指定后台Worker是否启用、启动并保持存活。"""
        if worker is None:
            return ComponentReadiness.DISABLED
        if failure is not None:
            return ComponentReadiness.DOWN
        if task is None:
            return ComponentReadiness.UNKNOWN
        if task.done():
            return ComponentReadiness.DOWN
        return ComponentReadiness.UP

    def _on_worker_done(
        self,
        task: asyncio.Task[None],
        *,
        worker_name: str,
    ) -> None:
        """及时消费后台任务异常，防止静默退出和未读取异常告警。"""
        if self._closing or self._closed:
            return
        worker_metadata = {
            "outbox": ("_worker_failure", "Outbox Worker"),
            "rca_consumer": (
                "_rca_consumer_failure",
                "RCA Consumer",
            ),
            "audit_retention": (
                "_audit_retention_failure",
                "Audit Retention Worker",
            ),
            "remediation_reclaim": (
                "_remediation_reclaim_failure",
                "Remediation Reclaim Worker",
            ),
            "ticket_submission_consumer": (
                "_ticket_submission_consumer_failure",
                "Ticket Submission Consumer",
            ),
        }
        try:
            failure_attribute, display_name = worker_metadata[worker_name]
        except KeyError as exc:
            raise RuntimeError(f"Unknown background worker: {worker_name}") from exc
        if task.cancelled():
            setattr(self, failure_attribute, "cancelled")
            logger.critical("%s在非停机阶段被取消", display_name)
            return

        error = task.exception()
        if error is None:
            setattr(self, failure_attribute, "unexpected_exit")
            logger.critical("%s在非停机阶段意外退出", display_name)
            return
        setattr(self, failure_attribute, type(error).__name__)
        logger.critical("%s异常退出", display_name)

    async def _close_resources(self, suppress_errors: bool) -> None:
        """尽力清理全部资源，并按调用场景决定是否报告清理错误。"""
        self._closing = True
        errors: list[CleanupFailure] = []
        if self.worker is not None:
            self.worker.request_stop()
        if self.rca_consumer_worker is not None:
            self.rca_consumer_worker.request_stop()
        if self.audit_retention_worker is not None:
            self.audit_retention_worker.request_stop()
        if self.remediation_reclaim_worker is not None:
            self.remediation_reclaim_worker.request_stop()
        if self.ticket_submission_consumer_worker is not None:
            self.ticket_submission_consumer_worker.request_stop()
        await self._await_worker_shutdown(
            task=self._worker_task,
            timeout_seconds=self.shutdown_timeout_seconds,
            stage_name="outbox_worker",
            display_name="Outbox Worker",
            errors=errors,
        )
        self._worker_task = None
        await self._await_worker_shutdown(
            task=self._rca_consumer_task,
            timeout_seconds=self.rca_consumer_shutdown_timeout_seconds,
            stage_name="rca_consumer",
            display_name="RCA Consumer",
            errors=errors,
        )
        self._rca_consumer_task = None
        await self._await_worker_shutdown(
            task=self._audit_retention_task,
            timeout_seconds=self.audit_retention_shutdown_timeout_seconds,
            stage_name="audit_retention",
            display_name="Audit Retention Worker",
            errors=errors,
        )
        self._audit_retention_task = None
        await self._await_worker_shutdown(
            task=self._remediation_reclaim_task,
            timeout_seconds=self.remediation_reclaim_shutdown_timeout_seconds,
            stage_name="remediation_reclaim",
            display_name="Remediation Reclaim Worker",
            errors=errors,
        )
        self._remediation_reclaim_task = None
        await self._await_worker_shutdown(
            task=self._ticket_submission_consumer_task,
            timeout_seconds=(self.ticket_submission_consumer_shutdown_timeout_seconds),
            stage_name="ticket_submission_consumer",
            display_name="Ticket Submission Consumer",
            errors=errors,
        )
        self._ticket_submission_consumer_task = None

        if self.publisher is not None:
            try:
                await self.publisher.close()
            except BaseException as exc:
                errors.append(("publisher", exc))

        if self.admin_authenticator is not None:
            try:
                await self.admin_authenticator.close()
            except BaseException as exc:
                errors.append(("admin_authenticator", exc))

        for resource in reversed(self.managed_resources):
            try:
                await resource.close()
            except BaseException as exc:
                errors.append(("managed_resource", exc))

        try:
            await self.engine.dispose()
        except BaseException as exc:
            errors.append(("database_engine", exc))

        self._started = False
        self._closed = True
        for stage_name, _ in errors:
            logger.error(
                "Application Runtime资源清理失败: %s",
                stage_name,
            )
        if errors and not suppress_errors:
            raise errors[0][1]

    @staticmethod
    async def _await_worker_shutdown(
        task: asyncio.Task[None] | None,
        timeout_seconds: float,
        stage_name: str,
        display_name: str,
        errors: list[CleanupFailure],
    ) -> None:
        """等待后台任务协作退出，超时后取消并收集异常。"""
        if task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                errors.append((stage_name, exc))
            logger.error("%s优雅停机超时，已强制取消", display_name)
        except BaseException as exc:
            errors.append((stage_name, exc))


def build_runtime(
    settings: Settings,
    report_observer: LLMReportGenerationObserverPort | None = None,
    ticketing_gateway: TicketingGatewayPort | None = None,
    ticketing_gateway_observer: TicketingGatewayObserverPort | None = None,
    notification_gateway: NotificationGatewayPort | None = None,
    remediation_executor: RemediationExecutorPort | None = None,
    remediation_action_catalog: RemediationActionCatalogPort | None = None,
) -> ApplicationRuntime:
    """装配数据库、业务服务及可选Outbox和RCA后台Worker。"""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    identifier_generator = UUIDIdentifierGenerator()

    def unit_of_work_factory() -> SQLAlchemyUnitOfWork:
        """为每个用例创建独立事务边界。"""
        return SQLAlchemyUnitOfWork(session_factory)

    alert_service = AlertApplicationService(
        unit_of_work_factory=unit_of_work_factory,
        incident_policy=IncidentCreationPolicy(),
        identifier_generator=identifier_generator,
    )
    rca_service = RCAApplicationService(
        unit_of_work_factory=unit_of_work_factory,
        identifier_generator=identifier_generator,
    )
    rca_query_service = RCAExecutionQueryService(
        unit_of_work_factory=unit_of_work_factory,
    )
    rca_cancellation_service = RCACancellationService(
        unit_of_work_factory=unit_of_work_factory,
        identifier_generator=identifier_generator,
    )
    incident_query_service = IncidentQueryService(
        unit_of_work_factory=unit_of_work_factory,
    )
    incident_resolution_service = IncidentResolutionService(
        unit_of_work_factory=unit_of_work_factory,
        identifier_generator=identifier_generator,
    )
    permission_admin_service = ToolPermissionAdminService(
        store=SQLAlchemyToolPermissionAdminStore(session_factory),
        identifier_generator=identifier_generator,
    )
    runbook_admin_service = RunbookAdminService(
        store=SQLAlchemyRunbookAdminStore(session_factory),
        identifier_generator=identifier_generator,
    )
    ticket_draft_service = TicketDraftApplicationService(
        unit_of_work_factory=unit_of_work_factory,
        identifier_generator=identifier_generator,
    )
    rca_feedback_service = RCAFeedbackApplicationService(
        unit_of_work_factory=unit_of_work_factory,
        identifier_generator=identifier_generator,
    )
    owned_remediation_executor: AsyncCloseable | None = None
    resolved_remediation_executor = remediation_executor
    resolved_remediation_action_catalog = remediation_action_catalog
    if (
        resolved_remediation_executor is None
        and settings.remediation_controller_base_url is not None
    ):
        owned_remediation_executor = HttpRemediationExecutor(
            HttpRemediationExecutorConfig(
                base_url=settings.remediation_controller_base_url,
                bearer_token=settings.remediation_controller_bearer_token,
                request_timeout_seconds=(settings.remediation_request_timeout_seconds),
                max_response_bytes=settings.remediation_max_response_bytes,
            )
        )
        resolved_remediation_executor = owned_remediation_executor
    if (
        resolved_remediation_action_catalog is None
        and settings.remediation_action_catalog_path is not None
    ):
        resolved_remediation_action_catalog = JsonRemediationActionCatalog(
            settings.remediation_action_catalog_path
        )
    if (resolved_remediation_executor is None) != (
        resolved_remediation_action_catalog is None
    ):
        raise ValueError(
            "Remediation executor and action catalog must be provided together"
        )
    remediation_policy = RemediationExecutionPolicy(
        execution_enabled=settings.remediation_execution_enabled,
        allowed_tenants=frozenset(settings.remediation_allowed_tenants),
        maintenance_start_hour_utc=(settings.remediation_maintenance_start_hour_utc),
        maintenance_end_hour_utc=(settings.remediation_maintenance_end_hour_utc),
    )
    remediation_service = (
        RemediationApplicationService(
            unit_of_work_factory=unit_of_work_factory,
            identifier_generator=identifier_generator,
            action_catalog=resolved_remediation_action_catalog,
            executor=resolved_remediation_executor,
            policy=remediation_policy,
            # claim 租约与外部调用超时分离：超时后靠 lease 过期收口，
            # 禁止永久卡在 EXECUTING。
            lease_seconds=settings.remediation_lease_seconds,
            request_timeout_seconds=settings.remediation_request_timeout_seconds,
        )
        if (
            resolved_remediation_executor is not None
            and resolved_remediation_action_catalog is not None
        )
        else None
    )
    ticket_submission_service = TicketSubmissionApplicationService(
        unit_of_work_factory=unit_of_work_factory,
        identifier_generator=identifier_generator,
    )
    admin_authenticator: AuthenticatorLifecycle | None = None
    if settings.admin_oidc_enabled:
        admin_authenticator = OIDCAdministratorAuthenticator(
            OIDCAuthenticatorConfig(
                issuer=settings.admin_oidc_issuer or "",
                audience=settings.admin_oidc_audience or "",
                jwks_url=settings.admin_oidc_jwks_url or "",
                algorithms=settings.admin_oidc_allowed_algorithms,
                jwks_cache_ttl_seconds=(settings.admin_oidc_jwks_cache_ttl_seconds),
                unknown_kid_cache_seconds=(
                    settings.admin_oidc_unknown_kid_cache_seconds
                ),
                request_timeout_seconds=(settings.admin_oidc_request_timeout_seconds),
                leeway_seconds=settings.admin_oidc_leeway_seconds,
                subject_claim=settings.admin_oidc_subject_claim,
                scopes_claim=settings.admin_oidc_scopes_claim,
                tenants_claim=settings.admin_oidc_tenants_claim,
                all_tenants_claim=(settings.admin_oidc_all_tenants_claim),
            )
        )
    elif settings.admin_demo_enabled:
        if settings.admin_demo_token is None:
            raise ValueError(
                "admin_demo_token is required when demo authentication is enabled"
            )
        admin_authenticator = DemoAdministratorAuthenticator(
            DemoAdministratorAuthenticatorConfig(
                token=settings.admin_demo_token,
                tenant_id=settings.admin_demo_tenant_id,
                admin_id=settings.admin_demo_admin_id,
            )
        )

    publisher: KafkaEventPublisher | None = None
    worker: OutboxWorkerRunner | None = None
    audit_retention_worker: AuditRetentionWorkerRunner | None = None
    remediation_reclaim_worker: RemediationReclaimWorkerRunner | None = None
    rca_consumer_worker: RCAConsumerRunner | None = None
    ticket_submission_consumer_worker: TicketSubmissionConsumerRunner | None
    ticket_submission_consumer_worker = None
    managed_resources: tuple[AsyncCloseable, ...] = ()
    owned_ticketing_gateway: AsyncCloseable | None = None
    owned_notification_gateway: AsyncCloseable | None = None
    outbox_backlog_monitor: OutboxBacklogMonitor | None = None
    kafka_password = (
        settings.kafka_sasl_password.get_secret_value()
        if settings.kafka_sasl_password is not None
        else None
    )
    if settings.metrics_enabled:
        outbox_backlog_monitor = OutboxBacklogMonitor(
            reader=SQLAlchemyOutboxMetricsReader(session_factory),
            config=OutboxBacklogMonitorConfig(
                query_timeout=timedelta(
                    seconds=settings.metrics_outbox_query_timeout_seconds
                ),
                cache_ttl=timedelta(seconds=settings.metrics_outbox_cache_ttl_seconds),
            ),
        )
    if settings.outbox_worker_enabled:
        worker_id = settings.outbox_worker_id or _default_worker_id()
        publisher = KafkaEventPublisher(
            KafkaPublisherConfig(
                bootstrap_servers=settings.kafka_servers,
                topic=settings.kafka_topic,
                client_id=settings.kafka_client_id,
                security_protocol=settings.kafka_security_protocol,
                sasl_mechanism=settings.kafka_sasl_mechanism,
                sasl_username=settings.kafka_sasl_username,
                sasl_password=kafka_password,
            )
        )
        dispatcher = OutboxDispatcher(
            store=SQLAlchemyOutboxDispatchStore(session_factory),
            publisher=publisher,
            worker_id=worker_id,
            config=OutboxDispatcherConfig(
                batch_size=settings.outbox_batch_size,
                max_attempts=settings.outbox_max_attempts,
                lease_duration=timedelta(seconds=settings.outbox_lease_seconds),
                publish_timeout=timedelta(
                    seconds=settings.outbox_publish_timeout_seconds
                ),
            ),
        )
        worker = OutboxWorkerRunner(
            dispatcher=dispatcher,
            worker_id=worker_id,
        )
    if settings.audit_retention_worker_enabled:
        retention_batch_size = settings.audit_retention_batch_size
        retention_service = AuditRetentionService(
            store=SQLAlchemyAuditRetentionStore(session_factory),
            config=AuditRetentionConfig(
                retention_period=timedelta(days=settings.audit_retention_days),
                batch_size=retention_batch_size,
            ),
        )
        audit_retention_worker = AuditRetentionWorkerRunner(
            service=retention_service,
            worker_id=(
                settings.audit_retention_worker_id
                or derive_suffixed_id(
                    _default_worker_id(),
                    "audit-retention",
                )
            ),
            config=AuditRetentionWorkerConfig(
                active_interval=timedelta(
                    seconds=(settings.audit_retention_active_interval_seconds)
                ),
                idle_interval=timedelta(
                    seconds=(settings.audit_retention_idle_interval_seconds)
                ),
                error_backoff_initial=timedelta(
                    seconds=(settings.audit_retention_error_backoff_initial_seconds)
                ),
                error_backoff_max=timedelta(
                    seconds=(settings.audit_retention_error_backoff_max_seconds)
                ),
                batch_size=retention_batch_size,
            ),
        )
    if settings.remediation_reclaim_worker_enabled:
        if remediation_service is None:
            raise ValueError(
                "Remediation reclaim worker requires configured remediation"
            )
        remediation_reclaim_worker = RemediationReclaimWorkerRunner(
            service=remediation_service,
            worker_id=(
                settings.remediation_reclaim_worker_id
                or derive_suffixed_id(
                    _default_worker_id(),
                    "remediation-reclaim",
                )
            ),
            config=RemediationReclaimWorkerConfig(
                interval=timedelta(
                    seconds=settings.remediation_reclaim_interval_seconds
                ),
                error_backoff_initial=timedelta(
                    seconds=(settings.remediation_reclaim_error_backoff_initial_seconds)
                ),
                error_backoff_max=timedelta(
                    seconds=(settings.remediation_reclaim_error_backoff_max_seconds)
                ),
                batch_size=settings.remediation_reclaim_batch_size,
            ),
        )
    if settings.rca_consumer_enabled:
        rca_bundle = build_rca_consumer_runtime(
            settings,
            session_factory,
            report_observer=report_observer,
        )
        rca_consumer_worker = rca_bundle.worker
        managed_resources = rca_bundle.resources
    resolved_ticketing_gateway = ticketing_gateway
    if resolved_ticketing_gateway is None:
        owned_ticketing_gateway = _build_configured_ticketing_gateway(
            settings,
            observer=ticketing_gateway_observer,
        )
        resolved_ticketing_gateway = owned_ticketing_gateway
    if settings.ticket_submission_consumer_enabled:
        ticket_submission_bundle = build_ticket_submission_consumer_runtime(
            settings,
            session_factory,
            ticketing_gateway=resolved_ticketing_gateway,
        )
        ticket_submission_consumer_worker = ticket_submission_bundle.worker
    if owned_ticketing_gateway is not None:
        managed_resources = (*managed_resources, owned_ticketing_gateway)
    resolved_notification_gateway = notification_gateway
    if resolved_notification_gateway is None:
        owned_notification_gateway = _build_configured_notification_gateway(settings)
        resolved_notification_gateway = owned_notification_gateway
    notification_service = (
        NotificationApplicationService(
            unit_of_work_factory=unit_of_work_factory,
            gateway=resolved_notification_gateway,
            identifier_generator=identifier_generator,
        )
        if resolved_notification_gateway is not None
        else None
    )
    if owned_notification_gateway is not None:
        managed_resources = (
            *managed_resources,
            owned_notification_gateway,
        )
    if owned_remediation_executor is not None:
        managed_resources = (
            *managed_resources,
            owned_remediation_executor,
        )

    return ApplicationRuntime(
        engine=engine,
        session_factory=session_factory,
        alert_service=alert_service,
        rca_service=rca_service,
        rca_query_service=rca_query_service,
        rca_cancellation_service=rca_cancellation_service,
        incident_query_service=incident_query_service,
        incident_resolution_service=incident_resolution_service,
        audit_retention_worker=audit_retention_worker,
        remediation_reclaim_worker=remediation_reclaim_worker,
        rca_consumer_worker=rca_consumer_worker,
        ticket_submission_consumer_worker=(ticket_submission_consumer_worker),
        rca_consumer_shutdown_timeout_seconds=(
            settings.rca_consumer_shutdown_timeout_seconds
        ),
        audit_retention_shutdown_timeout_seconds=(
            settings.audit_retention_shutdown_timeout_seconds
        ),
        remediation_reclaim_shutdown_timeout_seconds=(
            settings.remediation_reclaim_shutdown_timeout_seconds
        ),
        ticket_submission_consumer_shutdown_timeout_seconds=(
            settings.ticket_submission_consumer_shutdown_timeout_seconds
        ),
        permission_admin_service=permission_admin_service,
        runbook_admin_service=runbook_admin_service,
        rca_feedback_service=rca_feedback_service,
        remediation_service=remediation_service,
        ticket_draft_service=ticket_draft_service,
        ticket_submission_service=ticket_submission_service,
        notification_service=notification_service,
        admin_authenticator=admin_authenticator,
        shutdown_timeout_seconds=settings.outbox_shutdown_timeout_seconds,
        readiness_database_timeout_seconds=(
            settings.readiness_database_timeout_seconds
        ),
        readiness_cache_ttl_seconds=settings.readiness_cache_ttl_seconds,
        publisher=publisher,
        worker=worker,
        outbox_backlog_monitor=outbox_backlog_monitor,
        managed_resources=managed_resources,
    )


def _build_configured_ticketing_gateway(
    settings: Settings,
    *,
    observer: TicketingGatewayObserverPort | None,
) -> AsyncCloseable | None:
    """按启用开关装配供应商路由，并保留通用网关的回退语义。"""
    routes: dict[str, TicketingGatewayPort] = {}
    fallback: TicketingGatewayPort | None = None
    if settings.ticketing_jira_enabled:
        assert settings.ticketing_jira_api_token is not None
        routes["jira"] = JiraTicketingGateway(
            JiraTicketingGatewayConfig(
                base_url=settings.ticketing_jira_base_url or "",
                user_email=settings.ticketing_jira_user_email or "",
                api_token=settings.ticketing_jira_api_token,
                project_key=settings.ticketing_jira_project_key or "",
                issue_type=settings.ticketing_jira_issue_type,
                request_timeout_seconds=(
                    settings.ticketing_jira_request_timeout_seconds
                ),
                max_response_bytes=(settings.ticketing_jira_max_response_bytes),
            ),
            observer=observer,
        )
    if settings.ticketing_servicenow_enabled:
        assert settings.ticketing_servicenow_password is not None
        routes["servicenow"] = ServiceNowTicketingGateway(
            ServiceNowTicketingGatewayConfig(
                base_url=settings.ticketing_servicenow_base_url or "",
                username=settings.ticketing_servicenow_username or "",
                password=settings.ticketing_servicenow_password,
                table=settings.ticketing_servicenow_table,
                request_timeout_seconds=(
                    settings.ticketing_servicenow_request_timeout_seconds
                ),
                max_response_bytes=(settings.ticketing_servicenow_max_response_bytes),
            ),
            observer=observer,
        )
    if settings.ticketing_http_json_enabled:
        fallback = HttpJsonTicketingGateway(
            HttpJsonTicketingGatewayConfig(
                endpoint_url=settings.ticketing_http_json_endpoint_url or "",
                bearer_token=settings.ticketing_http_json_bearer_token,
                request_timeout_seconds=(
                    settings.ticketing_http_json_request_timeout_seconds
                ),
                max_response_bytes=(settings.ticketing_http_json_max_response_bytes),
            ),
            observer=observer,
        )
    if routes:
        return RoutingTicketingGateway(routes, fallback=fallback)
    return fallback


def _build_configured_notification_gateway(
    settings: Settings,
) -> AsyncCloseable | None:
    """装配显式配置的 Slack、Teams 和 PagerDuty 通知路由。"""
    routes: dict[str, NotificationGatewayPort] = {}
    for provider, credential in (
        ("slack", settings.notification_slack_webhook_url),
        ("teams", settings.notification_teams_webhook_url),
        (
            "pagerduty",
            settings.notification_pagerduty_routing_key,
        ),
    ):
        if credential is None:
            continue
        endpoint = (
            credential.get_secret_value() if provider in {"slack", "teams"} else None
        )
        routes[provider] = WebhookNotificationGateway(
            NotificationWebhookConfig(
                provider=provider,
                credential=credential,
                endpoint_url=endpoint,
                request_timeout_seconds=(settings.notification_request_timeout_seconds),
                max_response_bytes=(settings.notification_max_response_bytes),
            )
        )
    return RoutingNotificationGateway(routes) if routes else None


def _default_worker_id() -> str:
    """生成进程级Worker标识，兼顾人工排障和字段长度限制。"""
    return derive_suffixed_id(gethostname(), str(os.getpid()))
