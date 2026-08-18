from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from devops_agent_platform import __version__
from devops_agent_platform.bootstrap.dependencies import (
    build_skeleton_alert_service,
    build_skeleton_change_event_service,
    build_skeleton_rca_service,
)
from devops_agent_platform.bootstrap.runtime import (
    ApplicationRuntime,
    build_runtime,
)
from devops_agent_platform.infrastructure.auth import (
    HMACAlertWebhookAuthenticator,
    HMACWebhookAuthenticatorConfig,
)
from devops_agent_platform.infrastructure.config.settings import (
    Settings,
    get_settings,
)
from devops_agent_platform.infrastructure.logging import configure_logging
from devops_agent_platform.infrastructure.metrics import ApplicationMetrics
from devops_agent_platform.interfaces.http.exception_handlers import (
    register_exception_handlers,
)
from devops_agent_platform.interfaces.http.middleware import TraceIdMiddleware
from devops_agent_platform.interfaces.http.rate_limit import (
    RateLimitConfig,
    RateLimitMiddleware,
)
from devops_agent_platform.interfaces.http.routes.alerts import router as alerts_router
from devops_agent_platform.interfaces.http.routes.change_events import (
    router as change_events_router,
)
from devops_agent_platform.interfaces.http.routes.console import (
    router as console_router,
)
from devops_agent_platform.interfaces.http.routes.dataset_releases import (
    router as dataset_releases_router,
)
from devops_agent_platform.interfaces.http.routes.health import router as health_router
from devops_agent_platform.interfaces.http.routes.incidents import (
    router as incidents_router,
)
from devops_agent_platform.interfaces.http.routes.metrics import (
    router as metrics_router,
)
from devops_agent_platform.interfaces.http.routes.notifications import (
    router as notifications_router,
)
from devops_agent_platform.interfaces.http.routes.rca_feedback import (
    router as rca_feedback_router,
)
from devops_agent_platform.interfaces.http.routes.rca_results import (
    router as rca_results_router,
)
from devops_agent_platform.interfaces.http.routes.remediation import (
    router as remediation_router,
)
from devops_agent_platform.interfaces.http.routes.runbooks import (
    router as runbooks_router,
)
from devops_agent_platform.interfaces.http.routes.ticket_drafts import (
    router as ticket_drafts_router,
)
from devops_agent_platform.interfaces.http.routes.tool_permissions import (
    router as tool_permissions_router,
)
from devops_agent_platform.interfaces.http.routes.workspaces import (
    router as workspaces_router,
)
from devops_agent_platform.ports.authentication import (
    AdministratorAuthenticatorPort,
    AlertWebhookAuthenticatorPort,
)

RuntimeFactory = Callable[[Settings], ApplicationRuntime]


def create_app(
    settings: Settings | None = None,
    runtime_enabled: bool = True,
    runtime_factory: RuntimeFactory | None = None,
    admin_authenticator: AdministratorAuthenticatorPort | None = None,
    alert_webhook_authenticator: (AlertWebhookAuthenticatorPort | None) = None,
) -> FastAPI:
    """创建并装配 FastAPI 应用。

    bootstrap 层只负责框架级装配，包括中间件、路由和异常处理器。
    业务用例必须留在 application 层，避免启动入口承载业务逻辑。
    """
    resolved_settings = settings or get_settings()
    if not runtime_enabled and resolved_settings.app_env.strip().lower() in {
        "prod",
        "production",
    }:
        raise ValueError("Skeleton runtime cannot be enabled in production")
    configure_logging(
        service_name=resolved_settings.service_name,
        environment=resolved_settings.app_env,
        level=resolved_settings.log_level,
    )
    application_metrics = (
        ApplicationMetrics() if resolved_settings.metrics_enabled else None
    )
    resolved_alert_webhook_authenticator = alert_webhook_authenticator
    if (
        resolved_alert_webhook_authenticator is None
        and resolved_settings.alert_webhook_auth_enabled
    ):
        assert resolved_settings.alert_webhook_secret is not None
        resolved_alert_webhook_authenticator = HMACAlertWebhookAuthenticator(
            HMACWebhookAuthenticatorConfig(
                secret=resolved_settings.alert_webhook_secret,
                tolerance_seconds=(resolved_settings.alert_webhook_tolerance_seconds),
                max_body_bytes=(resolved_settings.alert_webhook_max_body_bytes),
            )
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = (
            runtime_factory(resolved_settings)
            if runtime_factory is not None
            else build_runtime(
                resolved_settings,
                report_observer=application_metrics,
                ticketing_gateway_observer=application_metrics,
            )
        )
        await runtime.start()
        app.state.runtime = runtime
        app.state.alert_application_service = runtime.alert_service
        app.state.change_event_application_service = getattr(
            runtime,
            "change_event_service",
            None,
        )
        app.state.rca_application_service = runtime.rca_service
        app.state.rca_query_service = getattr(
            runtime,
            "rca_query_service",
            None,
        )
        app.state.rca_feedback_service = getattr(
            runtime,
            "rca_feedback_service",
            None,
        )
        app.state.remediation_service = getattr(
            runtime,
            "remediation_service",
            None,
        )
        app.state.rca_cancellation_service = getattr(
            runtime,
            "rca_cancellation_service",
            None,
        )
        app.state.incident_query_service = getattr(
            runtime,
            "incident_query_service",
            None,
        )
        app.state.incident_resolution_service = getattr(
            runtime,
            "incident_resolution_service",
            None,
        )
        app.state.tool_permission_admin_service = getattr(
            runtime,
            "permission_admin_service",
            None,
        )
        app.state.runbook_admin_service = getattr(
            runtime,
            "runbook_admin_service",
            None,
        )
        app.state.workspace_service = getattr(runtime, "workspace_service", None)
        app.state.dataset_release_service = getattr(
            runtime,
            "dataset_release_service",
            None,
        )
        app.state.ticket_draft_service = getattr(
            runtime,
            "ticket_draft_service",
            None,
        )
        app.state.ticket_submission_service = getattr(
            runtime,
            "ticket_submission_service",
            None,
        )
        app.state.notification_service = getattr(
            runtime,
            "notification_service",
            None,
        )
        app.state.admin_authenticator = admin_authenticator or getattr(
            runtime, "admin_authenticator", None
        )
        try:
            yield
        finally:
            app.state.alert_application_service = None
            app.state.change_event_application_service = None
            app.state.rca_application_service = None
            app.state.rca_query_service = None
            app.state.rca_feedback_service = None
            app.state.remediation_service = None
            app.state.rca_cancellation_service = None
            app.state.incident_query_service = None
            app.state.incident_resolution_service = None
            app.state.tool_permission_admin_service = None
            app.state.runbook_admin_service = None
            app.state.workspace_service = None
            app.state.dataset_release_service = None
            app.state.ticket_draft_service = None
            app.state.ticket_submission_service = None
            app.state.notification_service = None
            app.state.admin_authenticator = admin_authenticator
            try:
                await runtime.close()
            finally:
                app.state.runtime = None

    app = FastAPI(
        title="DevOps Intelligent Troubleshooting Agent Platform",
        version=__version__,
        lifespan=lifespan if runtime_enabled else None,
    )
    app.state.runtime = None
    app.state.alert_application_service = None
    app.state.change_event_application_service = None
    app.state.rca_application_service = None
    app.state.rca_query_service = None
    app.state.rca_feedback_service = None
    app.state.remediation_service = None
    app.state.rca_cancellation_service = None
    app.state.incident_query_service = None
    app.state.incident_resolution_service = None
    app.state.tool_permission_admin_service = None
    app.state.runbook_admin_service = None
    app.state.workspace_service = None
    app.state.dataset_release_service = None
    app.state.ticket_draft_service = None
    app.state.ticket_submission_service = None
    app.state.notification_service = None
    app.state.admin_authenticator = admin_authenticator
    app.state.alert_webhook_authenticator = resolved_alert_webhook_authenticator
    app.state.metrics = application_metrics
    if not runtime_enabled:
        app.state.alert_application_service = build_skeleton_alert_service()
        app.state.change_event_application_service = (
            build_skeleton_change_event_service()
        )
        app.state.rca_application_service = build_skeleton_rca_service()
    if resolved_settings.http_rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            config=RateLimitConfig(
                requests=resolved_settings.http_rate_limit_requests,
                window_seconds=(resolved_settings.http_rate_limit_window_seconds),
                max_keys=resolved_settings.http_rate_limit_max_keys,
            ),
        )
    app.add_middleware(
        TraceIdMiddleware,
        log_health_endpoints=(resolved_settings.http_access_log_health_endpoints),
        metrics=app.state.metrics,
    )
    register_exception_handlers(app)
    app.include_router(console_router)
    app.include_router(health_router)
    app.include_router(alerts_router, prefix="/api/v1")
    app.include_router(change_events_router, prefix="/api/v1")
    app.include_router(incidents_router, prefix="/api/v1")
    app.include_router(rca_results_router, prefix="/api/v1")
    app.include_router(rca_feedback_router, prefix="/api/v1")
    app.include_router(remediation_router, prefix="/api/v1")
    app.include_router(tool_permissions_router, prefix="/api/v1")
    app.include_router(runbooks_router, prefix="/api/v1")
    app.include_router(workspaces_router, prefix="/api/v1")
    app.include_router(dataset_releases_router, prefix="/api/v1")
    app.include_router(ticket_drafts_router, prefix="/api/v1")
    app.include_router(notifications_router, prefix="/api/v1")
    if resolved_settings.metrics_enabled:
        app.include_router(metrics_router)
    return app
