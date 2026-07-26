import asyncio
import logging
from collections.abc import MutableSequence
from typing import Any

import pytest
from pydantic import SecretStr

from devops_agent_platform.application.services import (
    ticket_submission_consumer_runner as ticket_consumer_runner,
)
from devops_agent_platform.application.services.audit_retention_worker import (
    AuditRetentionWorkerRunner,
)
from devops_agent_platform.application.services.incident_query_service import (
    IncidentQueryService,
)
from devops_agent_platform.application.services.incident_resolution_service import (
    IncidentResolutionService,
)
from devops_agent_platform.application.services.outbox_worker import (
    OutboxWorkerRunner,
)
from devops_agent_platform.application.services.rca_cancellation_service import (
    RCACancellationService,
)
from devops_agent_platform.application.services.rca_consumer_runner import (
    RCAConsumerRunner,
)
from devops_agent_platform.application.services.runbook_admin_service import (
    RunbookAdminService,
)
from devops_agent_platform.application.services.ticket_draft_service import (
    TicketDraftApplicationService,
)
from devops_agent_platform.bootstrap import runtime as runtime_module
from devops_agent_platform.bootstrap.dependencies import (
    build_skeleton_alert_service,
    build_skeleton_rca_service,
)
from devops_agent_platform.bootstrap.runtime import (
    ApplicationRuntime,
    build_runtime,
)
from devops_agent_platform.bootstrap.worker_identity import derive_suffixed_id
from devops_agent_platform.infrastructure.auth import (
    DemoAdministratorAuthenticator,
    OIDCAdministratorAuthenticator,
)
from devops_agent_platform.infrastructure.config.settings import Settings
from devops_agent_platform.infrastructure.ticketing import (
    HttpJsonTicketingGateway,
    JiraTicketingGateway,
    RoutingTicketingGateway,
    ServiceNowTicketingGateway,
)
from devops_agent_platform.ports.ticketing import (
    TicketingSubmitOutcome,
    TicketingSubmitRequest,
)

TicketSubmissionConsumerRunner = (
    ticket_consumer_runner.TicketSubmissionConsumerRunner
)


class FakeConnection:
    """记录数据库探活，不执行真实SQL。"""

    def __init__(self, events: MutableSequence[str]) -> None:
        self._events = events

    async def execute(self, statement: Any) -> None:
        """模拟执行轻量探活语句。"""
        del statement
        self._events.append("database-check")


class FakeConnectionContext:
    """提供与AsyncEngine.connect兼容的异步上下文。"""

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback


class FakeEngine:
    """记录连接池探活和释放动作。"""

    def __init__(
        self,
        events: MutableSequence[str],
        *,
        dispose_error: BaseException | None = None,
    ) -> None:
        self._events = events
        self._dispose_error = dispose_error

    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext(FakeConnection(self._events))

    async def dispose(self) -> None:
        self._events.append("engine-dispose")
        if self._dispose_error is not None:
            raise self._dispose_error


class ToggleProbeConnection:
    """根据引擎状态返回成功、失败或阻塞的数据库探活连接。"""

    def __init__(self, engine: "ToggleProbeEngine") -> None:
        self._engine = engine

    async def execute(self, statement: Any) -> None:
        del statement
        self._engine.probe_count += 1
        self._engine.events.append("database-check")
        if self._engine.block_probe:
            self._engine.probe_started.set()
            await self._engine.probe_release.wait()
        if self._engine.probe_error is not None:
            raise self._engine.probe_error
        if not self._engine.available:
            raise ConnectionError("database unavailable")


class ToggleProbeContext:
    """包装可切换状态的数据库连接。"""

    def __init__(self, engine: "ToggleProbeEngine") -> None:
        self._engine = engine

    async def __aenter__(self) -> ToggleProbeConnection:
        return ToggleProbeConnection(self._engine)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback


class ToggleProbeEngine:
    """支持在启动后切换可用性的数据库引擎替身。"""

    def __init__(self, events: MutableSequence[str]) -> None:
        self.events = events
        self.available = True
        self.block_probe = False
        self.probe_count = 0
        self.probe_started = asyncio.Event()
        self.probe_release = asyncio.Event()
        self.probe_error: Exception | None = None

    def connect(self) -> ToggleProbeContext:
        return ToggleProbeContext(self)

    async def dispose(self) -> None:
        self.events.append("engine-dispose")


class FakeClock:
    """提供可手动推进的单调时钟。"""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class RecordingPublisher:
    """记录发布器启停，并可注入启动或关闭故障。"""

    def __init__(
        self,
        events: MutableSequence[str],
        *,
        fail_start: bool = False,
        fail_close: bool = False,
        start_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self._events = events
        self._start_error = start_error
        if self._start_error is None and fail_start:
            self._start_error = RuntimeError("publisher start failed")
        self._close_error = close_error
        if self._close_error is None and fail_close:
            self._close_error = RuntimeError("publisher close failed")

    async def start(self) -> None:
        self._events.append("publisher-start")
        if self._start_error is not None:
            raise self._start_error

    async def close(self) -> None:
        self._events.append("publisher-close")
        if self._close_error is not None:
            raise self._close_error


class RecordingAuthenticator:
    """记录认证器关闭动作的生命周期替身。"""

    def __init__(self, events: MutableSequence[str]) -> None:
        self._events = events

    async def authenticate(self, bearer_token: str):
        del bearer_token
        raise AssertionError("authenticate is not used in runtime tests")

    async def close(self) -> None:
        self._events.append("authenticator-close")


class FakeTicketingGateway:
    """构建 Runtime 时注入的外部工单端口替身。"""

    async def submit_ticket(
        self,
        request: TicketingSubmitRequest,
    ) -> TicketingSubmitOutcome:
        del request
        raise AssertionError("gateway is not used during runtime assembly")


class FakeTicketingGatewayObserver:
    """运行时构建 HTTP 工单网关时注入的观察端口替身。"""

    def observe_ticketing_gateway_submit(
        self,
        outcome,
        duration_seconds: float,
    ) -> None:
        del outcome, duration_seconds


class RecordingResource:
    """记录 Runtime 托管异步资源的关闭顺序。"""

    def __init__(
        self,
        events: MutableSequence[str],
        label: str,
        *,
        fail_close: bool = False,
        close_error: BaseException | None = None,
    ) -> None:
        self._events = events
        self._label = label
        self._close_error = close_error
        if self._close_error is None and fail_close:
            self._close_error = RuntimeError(f"{self._label} close failed")

    async def close(self) -> None:
        self._events.append(f"{self._label}-close")
        if self._close_error is not None:
            raise self._close_error


class CooperativeWorker:
    """收到停止信号后正常退出的Worker替身。"""

    def __init__(
        self,
        events: MutableSequence[str],
        label: str = "worker",
    ) -> None:
        self._events = events
        self._label = label
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        self._events.append(f"{self._label}-start")
        await self._stop_event.wait()
        self._events.append(f"{self._label}-finish")

    def request_stop(self) -> None:
        self._events.append(f"{self._label}-stop-request")
        self._stop_event.set()


class BlockingWorker:
    """忽略协作式停止，用于验证超时后的强制取消。"""

    def __init__(self, events: MutableSequence[str]) -> None:
        self._events = events
        self.cancelled = False

    async def run(self) -> None:
        self._events.append("worker-start")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            self._events.append("worker-cancelled")
            raise

    def request_stop(self) -> None:
        self._events.append("worker-stop-request")


class CrashingWorker:
    """启动成功后按测试信号异常退出的Worker替身。"""

    def __init__(
        self,
        events: MutableSequence[str],
        label: str = "worker",
        error: BaseException | None = None,
    ) -> None:
        self._events = events
        self._label = label
        self._error = error or RuntimeError("worker crashed")
        self.release = asyncio.Event()

    async def run(self) -> None:
        self._events.append(f"{self._label}-start")
        await self.release.wait()
        raise self._error

    def request_stop(self) -> None:
        self._events.append(f"{self._label}-stop-request")
        self.release.set()


def make_runtime(
    events: MutableSequence[str],
    *,
    engine: FakeEngine | ToggleProbeEngine | None = None,
    publisher: RecordingPublisher | None = None,
    admin_authenticator: RecordingAuthenticator | None = None,
    worker: CooperativeWorker | BlockingWorker | CrashingWorker | None = None,
    rca_consumer_worker: (
        CooperativeWorker | BlockingWorker | CrashingWorker | None
    ) = None,
    audit_retention_worker: (
        CooperativeWorker | BlockingWorker | CrashingWorker | None
    ) = None,
    ticket_submission_consumer_worker: (
        CooperativeWorker | BlockingWorker | CrashingWorker | None
    ) = None,
    shutdown_timeout_seconds: float = 0.1,
    rca_consumer_shutdown_timeout_seconds: float = 0.1,
    ticket_submission_consumer_shutdown_timeout_seconds: float = 0.1,
    readiness_database_timeout_seconds: float = 0.1,
    readiness_cache_ttl_seconds: float = 2.0,
    readiness_clock: FakeClock | None = None,
    managed_resources: tuple[RecordingResource, ...] = (),
) -> ApplicationRuntime:
    """构造不访问外部基础设施的运行时。"""
    return ApplicationRuntime(
        engine=engine or FakeEngine(events),  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        alert_service=build_skeleton_alert_service(),
        rca_service=build_skeleton_rca_service(),
        shutdown_timeout_seconds=shutdown_timeout_seconds,
        rca_consumer_shutdown_timeout_seconds=(
            rca_consumer_shutdown_timeout_seconds
        ),
        readiness_database_timeout_seconds=readiness_database_timeout_seconds,
        readiness_cache_ttl_seconds=readiness_cache_ttl_seconds,
        publisher=publisher,
        admin_authenticator=admin_authenticator,
        worker=worker,
        rca_consumer_worker=rca_consumer_worker,
        audit_retention_worker=audit_retention_worker,
        ticket_submission_consumer_worker=(
            ticket_submission_consumer_worker
        ),
        ticket_submission_consumer_shutdown_timeout_seconds=(
            ticket_submission_consumer_shutdown_timeout_seconds
        ),
        managed_resources=managed_resources,
        readiness_clock=readiness_clock or FakeClock(),
    )


async def test_runtime_starts_and_stops_resources_in_dependency_order() -> None:
    events: list[str] = []
    publisher = RecordingPublisher(events)
    worker = CooperativeWorker(events)
    runtime = make_runtime(events, publisher=publisher, worker=worker)

    await runtime.start()
    assert runtime.started is True

    await runtime.close()

    assert events == [
        "database-check",
        "publisher-start",
        "worker-start",
        "worker-stop-request",
        "worker-finish",
        "publisher-close",
        "engine-dispose",
    ]
    assert runtime.started is False


async def test_runtime_closes_authenticator_before_database_engine() -> None:
    """认证HTTP连接池必须在数据库引擎前释放。"""
    events: list[str] = []
    authenticator = RecordingAuthenticator(events)
    runtime = make_runtime(
        events,
        admin_authenticator=authenticator,
    )
    await runtime.start()

    await runtime.close()

    assert events == [
        "database-check",
        "authenticator-close",
        "engine-dispose",
    ]


async def test_runtime_closes_managed_resources_in_reverse_order() -> None:
    """Worker 停止后按创建逆序关闭 HTTP 资源，再释放数据库。"""
    events: list[str] = []
    worker = CooperativeWorker(events, label="rca")
    first = RecordingResource(events, "first")
    second = RecordingResource(events, "second")
    runtime = make_runtime(
        events,
        rca_consumer_worker=worker,
        managed_resources=(first, second),
    )
    await runtime.start()

    await runtime.close()

    assert events == [
        "database-check",
        "rca-start",
        "rca-stop-request",
        "rca-finish",
        "second-close",
        "first-close",
        "engine-dispose",
    ]


async def test_resource_close_failure_does_not_skip_remaining_cleanup() -> None:
    """单个连接池关闭失败时仍需释放其它连接池和数据库。"""
    events: list[str] = []
    first = RecordingResource(events, "first")
    second = RecordingResource(events, "second", fail_close=True)
    runtime = make_runtime(
        events,
        managed_resources=(first, second),
    )
    await runtime.start()

    with pytest.raises(RuntimeError, match="second close failed"):
        await runtime.close()

    assert events[-3:] == [
        "second-close",
        "first-close",
        "engine-dispose",
    ]


async def test_build_runtime_constructs_selected_admin_authenticator() -> None:
    """管理员认证默认关闭，本地演练与OIDC按配置互斥装配。"""
    disabled = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )
    enabled = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            admin_oidc_enabled=True,
            admin_oidc_issuer="https://identity.example.com/",
            admin_oidc_audience="devops-agent-api",
            admin_oidc_jwks_url=(
                "https://identity.example.com/.well-known/jwks.json"
            ),
        )
    )
    demo = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            admin_demo_enabled=True,
            admin_demo_token="local-demo-administrator-token-123456",
            admin_demo_tenant_id="tenant-demo",
            admin_demo_admin_id="admin-demo",
        )
    )
    try:
        assert disabled.admin_authenticator is None
        assert isinstance(
            disabled.runbook_admin_service,
            RunbookAdminService,
        )
        assert isinstance(
            disabled.incident_resolution_service,
            IncidentResolutionService,
        )
        assert isinstance(
            disabled.incident_query_service,
            IncidentQueryService,
        )
        assert isinstance(
            disabled.rca_cancellation_service,
            RCACancellationService,
        )
        assert isinstance(
            disabled.ticket_draft_service,
            TicketDraftApplicationService,
        )
        assert isinstance(
            enabled.admin_authenticator,
            OIDCAdministratorAuthenticator,
        )
        assert isinstance(
            demo.admin_authenticator,
            DemoAdministratorAuthenticator,
        )
    finally:
        await disabled.close()
        await enabled.close()
        await demo.close()


async def test_build_runtime_keeps_retention_disabled_by_default() -> None:
    """审计删除能力必须由部署环境显式开启。"""
    disabled = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )
    enabled = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            audit_retention_worker_enabled=True,
        )
    )
    try:
        assert disabled.audit_retention_worker is None
        assert isinstance(
            enabled.audit_retention_worker,
            AuditRetentionWorkerRunner,
        )
    finally:
        await disabled.close()
        await enabled.close()


async def test_default_background_worker_ids_use_hashed_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认后台Worker ID过长时不能靠切片静默截断。"""
    hostname = "worker-host-" + "x" * 180
    monkeypatch.setattr(runtime_module, "gethostname", lambda: hostname)
    monkeypatch.setattr(runtime_module.os, "getpid", lambda: 4321)
    runtime = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            outbox_worker_enabled=True,
            audit_retention_worker_enabled=True,
        )
    )

    try:
        assert isinstance(runtime.worker, OutboxWorkerRunner)
        assert isinstance(
            runtime.audit_retention_worker,
            AuditRetentionWorkerRunner,
        )
        default_worker_id = derive_suffixed_id(hostname, "4321")
        audit_worker_id = derive_suffixed_id(
            default_worker_id,
            "audit-retention",
        )
        assert runtime.worker._worker_id == default_worker_id
        assert len(runtime.worker._worker_id) == 128
        assert runtime.worker._worker_id.endswith("-4321")
        assert runtime.worker._worker_id != f"{hostname}-4321"[:128]
        assert runtime.audit_retention_worker._worker_id == audit_worker_id
        assert len(runtime.audit_retention_worker._worker_id) == 128
        assert runtime.audit_retention_worker._worker_id.endswith(
            "-audit-retention",
        )
        legacy_audit_id = f"{default_worker_id[:111]}-audit-retention"[:128]
        assert runtime.audit_retention_worker._worker_id != legacy_audit_id
    finally:
        await runtime.close()


async def test_build_runtime_optionally_constructs_rca_consumer() -> None:
    """真实 RCA 消费链路必须由完整部署配置显式开启。"""
    disabled = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )
    enabled = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            rca_consumer_enabled=True,
            prometheus_base_url="https://prometheus.example.com",
            loki_base_url="https://loki.example.com",
            tempo_base_url="https://tempo.example.com",
        )
    )
    try:
        assert disabled.rca_consumer_worker is None
        assert disabled.ticket_submission_consumer_worker is None
        assert disabled.managed_resources == ()
        assert isinstance(
            enabled.rca_consumer_worker,
            RCAConsumerRunner,
        )
        assert enabled.ticket_submission_consumer_worker is None
        assert len(enabled.managed_resources) == 3
    finally:
        await disabled.close()
        await enabled.close()


async def test_build_runtime_optionally_constructs_ticket_consumer() -> None:
    """真实工单提交消费链路必须显式开启并注入网关。"""
    disabled = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )
    enabled = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            ticket_submission_consumer_enabled=True,
        ),
        ticketing_gateway=FakeTicketingGateway(),
    )
    try:
        assert disabled.ticket_submission_consumer_worker is None
        assert isinstance(
            enabled.ticket_submission_consumer_worker,
            TicketSubmissionConsumerRunner,
        )
    finally:
        await disabled.close()
        await enabled.close()


async def test_build_runtime_can_construct_http_json_ticketing_gateway() -> None:
    """显式配置 HTTP JSON 网关时 Runtime 应托管其连接池。"""
    observer = FakeTicketingGatewayObserver()
    runtime = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            ticket_submission_consumer_enabled=True,
            ticketing_http_json_enabled=True,
            ticketing_http_json_endpoint_url=(
                "https://ticketing.example.com/api/tickets"
            ),
        ),
        ticketing_gateway_observer=observer,
    )
    try:
        assert isinstance(
            runtime.ticket_submission_consumer_worker,
            TicketSubmissionConsumerRunner,
        )
        assert len(runtime.managed_resources) == 1
        gateway = runtime.managed_resources[0]
        assert isinstance(gateway, HttpJsonTicketingGateway)
        assert gateway._observer is observer
        assert gateway._http_client.is_closed is False
    finally:
        await runtime.close()

    assert gateway._http_client.is_closed is True


async def test_build_runtime_constructs_vendor_ticketing_router() -> None:
    """Jira 与 ServiceNow 可同时启用并由 Runtime 统一托管。"""
    observer = FakeTicketingGatewayObserver()
    runtime = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            ticket_submission_consumer_enabled=True,
            ticketing_jira_enabled=True,
            ticketing_jira_base_url="https://example.atlassian.net",
            ticketing_jira_user_email="ops@example.com",
            ticketing_jira_api_token=SecretStr("jira-secret"),
            ticketing_jira_project_key="OPS",
            ticketing_servicenow_enabled=True,
            ticketing_servicenow_base_url=(
                "https://example.service-now.com"
            ),
            ticketing_servicenow_username="devops.integration",
            ticketing_servicenow_password=SecretStr("snow-secret"),
        ),
        ticketing_gateway_observer=observer,
    )
    try:
        assert len(runtime.managed_resources) == 1
        gateway = runtime.managed_resources[0]
        assert isinstance(gateway, RoutingTicketingGateway)
        assert isinstance(gateway._routes["jira"], JiraTicketingGateway)
        assert isinstance(
            gateway._routes["servicenow"],
            ServiceNowTicketingGateway,
        )
        jira_support = gateway._routes["jira"]._support
        snow_support = gateway._routes["servicenow"]._support
        assert jira_support.observer is observer
        assert snow_support.observer is observer
    finally:
        await runtime.close()

    assert jira_support.http_client.is_closed is True
    assert snow_support.http_client.is_closed is True


async def test_injected_ticket_gateway_is_not_runtime_managed() -> None:
    """外部注入的工单端口生命周期仍由上层容器负责。"""
    runtime = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            ticket_submission_consumer_enabled=True,
        ),
        ticketing_gateway=FakeTicketingGateway(),
    )
    try:
        assert isinstance(
            runtime.ticket_submission_consumer_worker,
            TicketSubmissionConsumerRunner,
        )
        assert runtime.managed_resources == ()
    finally:
        await runtime.close()


def test_build_runtime_requires_ticket_gateway_when_enabled() -> None:
    """开启工单提交消费但缺少网关时必须启动前失败。"""
    with pytest.raises(ValueError, match="gateway"):
        build_runtime(
            Settings(
                _env_file=None,
                database_url="sqlite+aiosqlite:///:memory:",
                ticket_submission_consumer_enabled=True,
            )
        )


async def test_start_failure_rolls_back_started_resources() -> None:
    events: list[str] = []
    publisher = RecordingPublisher(events, fail_start=True)
    runtime = make_runtime(events, publisher=publisher)

    with pytest.raises(RuntimeError, match="publisher start failed"):
        await runtime.start()

    assert events == [
        "database-check",
        "publisher-start",
        "publisher-close",
        "engine-dispose",
    ]
    assert runtime.started is False


async def test_start_rollback_logs_cleanup_stages_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """启动回滚应报告全部失败阶段，但不能记录底层异常正文。"""
    events: list[str] = []
    publisher = RecordingPublisher(
        events,
        start_error=RuntimeError("token=primary-secret"),
        close_error=RuntimeError("password=publisher-secret"),
    )
    resource = RecordingResource(
        events,
        "llm",
        close_error=RuntimeError("api_key=resource-secret"),
    )
    engine = FakeEngine(
        events,
        dispose_error=RuntimeError("password=database-secret"),
    )
    runtime = make_runtime(
        events,
        engine=engine,
        publisher=publisher,
        managed_resources=(resource,),
    )

    with caplog.at_level(
        logging.ERROR,
        logger="devops_agent_platform.bootstrap.runtime",
    ):
        with pytest.raises(RuntimeError, match="primary-secret"):
            await runtime.start()

    messages = [record.getMessage() for record in caplog.records]
    assert "Application Runtime资源清理失败: publisher" in messages
    assert "Application Runtime资源清理失败: managed_resource" in messages
    assert "Application Runtime资源清理失败: database_engine" in messages
    assert all(record.exc_info is None for record in caplog.records)
    assert "primary-secret" not in caplog.text
    assert "publisher-secret" not in caplog.text
    assert "resource-secret" not in caplog.text
    assert "database-secret" not in caplog.text
    assert events[-3:] == [
        "publisher-close",
        "llm-close",
        "engine-dispose",
    ]


async def test_close_logs_all_failure_stages_before_raising_first_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """正常关停也要记录后续失败，同时向调用方保留首个原始异常。"""
    events: list[str] = []
    publisher = RecordingPublisher(
        events,
        close_error=RuntimeError("password=publisher-secret"),
    )
    resource = RecordingResource(
        events,
        "tempo",
        close_error=RuntimeError("token=resource-secret"),
    )
    runtime = make_runtime(
        events,
        publisher=publisher,
        managed_resources=(resource,),
    )
    await runtime.start()

    with caplog.at_level(
        logging.ERROR,
        logger="devops_agent_platform.bootstrap.runtime",
    ):
        with pytest.raises(RuntimeError, match="publisher-secret"):
            await runtime.close()

    messages = [record.getMessage() for record in caplog.records]
    assert "Application Runtime资源清理失败: publisher" in messages
    assert "Application Runtime资源清理失败: managed_resource" in messages
    assert all(record.exc_info is None for record in caplog.records)
    assert "publisher-secret" not in caplog.text
    assert "resource-secret" not in caplog.text


async def test_shutdown_timeout_cancels_worker_and_releases_resources() -> None:
    events: list[str] = []
    publisher = RecordingPublisher(events)
    worker = BlockingWorker(events)
    runtime = make_runtime(
        events,
        publisher=publisher,
        worker=worker,
        shutdown_timeout_seconds=0.001,
    )
    await runtime.start()

    await runtime.close()

    assert worker.cancelled is True
    assert events[-3:] == [
        "worker-cancelled",
        "publisher-close",
        "engine-dispose",
    ]


async def test_cleanup_continues_when_publisher_close_fails() -> None:
    events: list[str] = []
    publisher = RecordingPublisher(events, fail_close=True)
    runtime = make_runtime(events, publisher=publisher)
    await runtime.start()

    with pytest.raises(RuntimeError, match="publisher close failed"):
        await runtime.close()

    assert events[-2:] == ["publisher-close", "engine-dispose"]


async def test_close_is_idempotent() -> None:
    events: list[str] = []
    runtime = make_runtime(events)
    await runtime.start()

    await runtime.close()
    await runtime.close()

    assert events.count("engine-dispose") == 1


async def test_readiness_reuses_database_probe_until_cache_expires() -> None:
    events: list[str] = []
    clock = FakeClock()
    engine = ToggleProbeEngine(events)
    runtime = make_runtime(
        events,
        engine=engine,
        readiness_clock=clock,
        readiness_cache_ttl_seconds=2,
    )
    await runtime.start()

    assert (await runtime.check_readiness()).ready is True
    assert engine.probe_count == 1

    clock.advance(2)
    engine.block_probe = True
    tasks = [
        asyncio.create_task(runtime.check_readiness())
        for _ in range(10)
    ]
    await engine.probe_started.wait()
    assert engine.probe_count == 2
    engine.probe_release.set()
    snapshots = await asyncio.gather(*tasks)

    assert all(snapshot.ready for snapshot in snapshots)
    assert engine.probe_count == 2
    await runtime.close()


async def test_database_failure_after_cache_expiry_marks_runtime_not_ready() -> None:
    events: list[str] = []
    clock = FakeClock()
    engine = ToggleProbeEngine(events)
    runtime = make_runtime(events, engine=engine, readiness_clock=clock)
    await runtime.start()
    engine.available = False
    clock.advance(2)

    snapshot = await runtime.check_readiness()

    assert snapshot.ready is False
    assert snapshot.unavailable_components == ("database",)
    assert snapshot.to_dict()["components"] == {
        "runtime": {"status": "up"},
        "database": {"status": "down"},
        "outbox_worker": {"status": "disabled"},
        "rca_consumer": {"status": "disabled"},
        "audit_retention": {"status": "disabled"},
        "ticket_submission_consumer": {"status": "disabled"},
    }
    await runtime.close()


async def test_database_readiness_failure_log_excludes_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """探活异常可改变健康状态，但异常正文不能进入日志。"""
    events: list[str] = []
    clock = FakeClock()
    engine = ToggleProbeEngine(events)
    runtime = make_runtime(events, engine=engine, readiness_clock=clock)
    await runtime.start()
    engine.probe_error = ConnectionError("password=database-secret")
    clock.advance(2)

    with caplog.at_level(
        logging.WARNING,
        logger="devops_agent_platform.bootstrap.runtime",
    ):
        snapshot = await runtime.check_readiness()

    assert snapshot.ready is False
    assert "数据库就绪检查失败" in [
        record.getMessage() for record in caplog.records
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "database-secret" not in caplog.text
    await runtime.close()


async def test_database_probe_timeout_marks_runtime_not_ready() -> None:
    events: list[str] = []
    clock = FakeClock()
    engine = ToggleProbeEngine(events)
    runtime = make_runtime(
        events,
        engine=engine,
        readiness_clock=clock,
        readiness_database_timeout_seconds=0.001,
    )
    await runtime.start()
    engine.block_probe = True
    clock.advance(2)

    snapshot = await runtime.check_readiness()

    assert snapshot.ready is False
    assert snapshot.unavailable_components == ("database",)
    await runtime.close()


async def test_worker_unexpected_exit_marks_runtime_not_ready() -> None:
    events: list[str] = []
    worker = CrashingWorker(events)
    runtime = make_runtime(events, worker=worker)
    await runtime.start()

    worker.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    snapshot = await runtime.check_readiness()

    assert snapshot.ready is False
    assert snapshot.unavailable_components == ("outbox_worker",)
    with pytest.raises(RuntimeError, match="worker crashed"):
        await runtime.close()
    assert events[-1] == "engine-dispose"


async def test_worker_crash_log_excludes_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Worker 崩溃必须告警并降级 readiness，但不能打印异常正文。"""
    events: list[str] = []
    worker = CrashingWorker(
        events,
        error=RuntimeError("token=worker-secret"),
    )
    runtime = make_runtime(events, worker=worker)
    await runtime.start()

    with caplog.at_level(
        logging.CRITICAL,
        logger="devops_agent_platform.bootstrap.runtime",
    ):
        worker.release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert runtime._worker_failure == "RuntimeError"
    assert "Outbox Worker异常退出" in [
        record.getMessage() for record in caplog.records
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "worker-secret" not in caplog.text
    with pytest.raises(RuntimeError, match="worker-secret"):
        await runtime.close()


async def test_runtime_manages_all_workers_independently() -> None:
    """全部 Worker 均应在依赖就绪后启动，并在资源关闭前退出。"""
    events: list[str] = []
    publisher = RecordingPublisher(events)
    outbox_worker = CooperativeWorker(events, label="outbox")
    rca_worker = CooperativeWorker(events, label="rca")
    retention_worker = CooperativeWorker(events, label="retention")
    ticket_worker = CooperativeWorker(events, label="ticket")
    runtime = make_runtime(
        events,
        publisher=publisher,
        worker=outbox_worker,
        rca_consumer_worker=rca_worker,
        audit_retention_worker=retention_worker,
        ticket_submission_consumer_worker=ticket_worker,
    )

    await runtime.start()
    snapshot = await runtime.check_readiness()
    await runtime.close()

    assert snapshot.to_dict()["components"]["outbox_worker"] == {
        "status": "up"
    }
    assert snapshot.to_dict()["components"]["rca_consumer"] == {
        "status": "up"
    }
    assert snapshot.to_dict()["components"]["audit_retention"] == {
        "status": "up"
    }
    assert snapshot.to_dict()["components"]["ticket_submission_consumer"] == {
        "status": "up"
    }
    assert events.index("database-check") < events.index("outbox-start")
    assert events.index("database-check") < events.index("rca-start")
    assert events.index("database-check") < events.index("retention-start")
    assert events.index("database-check") < events.index("ticket-start")
    assert events.index("outbox-finish") < events.index("publisher-close")
    assert events.index("rca-finish") < events.index("publisher-close")
    assert events.index("retention-finish") < events.index("publisher-close")
    assert events.index("ticket-finish") < events.index("publisher-close")
    assert events[-1] == "engine-dispose"


async def test_rca_consumer_crash_marks_only_its_readiness_down() -> None:
    """RCA任务崩溃应有独立组件状态，不污染Outbox状态。"""
    events: list[str] = []
    rca_worker = CrashingWorker(events, label="rca")
    runtime = make_runtime(events, rca_consumer_worker=rca_worker)
    await runtime.start()

    rca_worker.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    snapshot = await runtime.check_readiness()

    assert snapshot.unavailable_components == ("rca_consumer",)
    assert snapshot.to_dict()["components"]["outbox_worker"] == {
        "status": "disabled"
    }
    with pytest.raises(RuntimeError, match="worker crashed"):
        await runtime.close()


async def test_ticket_submission_consumer_crash_marks_only_it_down() -> None:
    """工单提交消费任务崩溃应有独立组件状态。"""
    events: list[str] = []
    ticket_worker = CrashingWorker(events, label="ticket")
    runtime = make_runtime(
        events,
        ticket_submission_consumer_worker=ticket_worker,
    )
    await runtime.start()

    ticket_worker.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    snapshot = await runtime.check_readiness()

    assert snapshot.unavailable_components == (
        "ticket_submission_consumer",
    )
    assert snapshot.to_dict()["components"]["rca_consumer"] == {
        "status": "disabled"
    }
    with pytest.raises(RuntimeError, match="worker crashed"):
        await runtime.close()


def test_runtime_rejects_invalid_ticket_consumer_shutdown_timeout() -> None:
    """工单提交消费停机超时必须为正数。"""
    with pytest.raises(ValueError, match="ticket_submission"):
        make_runtime(
            [],
            ticket_submission_consumer_shutdown_timeout_seconds=0,
        )


async def test_build_runtime_keeps_ticket_submission_consumer_disabled() -> None:
    """真实工单提交消费适配器完成前，默认运行时不启动该Worker。"""
    runtime = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )
    try:
        assert runtime.ticket_submission_consumer_worker is None
        assert not isinstance(
            runtime.ticket_submission_consumer_worker,
            TicketSubmissionConsumerRunner,
        )
    finally:
        await runtime.close()


async def test_build_runtime_wires_remediation_with_kill_switch_off() -> None:
    runtime = build_runtime(
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            remediation_controller_base_url="https://automation.example",
            remediation_action_catalog_path=(
                "ops/remediation/actions.example.json"
            ),
        )
    )
    try:
        assert runtime.remediation_service is not None
        assert (
            runtime.remediation_service._policy.execution_enabled is False  # noqa: SLF001
        )
    finally:
        await runtime.close()
