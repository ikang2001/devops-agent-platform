import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from devops_agent_platform.bootstrap.runtime import ApplicationRuntime
from devops_agent_platform.infrastructure.metrics import ApplicationMetrics

router = APIRouter(tags=["operations"])
logger = logging.getLogger(__name__)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """刷新Runtime内存指标并返回Prometheus文本协议。"""
    application_metrics: ApplicationMetrics = request.app.state.metrics
    runtime: ApplicationRuntime | None = request.app.state.runtime
    component_statuses = None
    worker_health = None
    rca_consumer_health = None
    audit_retention_health = None
    remediation_reclaim_health = None
    ticket_submission_consumer_health = None
    backlog_report = None
    if runtime is not None:
        rca_consumer_health = getattr(
            runtime,
            "rca_consumer_health",
            None,
        )
        audit_retention_health = getattr(
            runtime,
            "audit_retention_health",
            None,
        )
        remediation_reclaim_health = getattr(
            runtime,
            "remediation_reclaim_health",
            None,
        )
        ticket_submission_consumer_health = getattr(
            runtime,
            "ticket_submission_consumer_health",
            None,
        )
        try:
            readiness = await runtime.check_readiness()
            component_statuses = tuple(
                (name, status.value) for name, status in readiness.components
            )
            worker_health = getattr(runtime, "worker_health", None)
        except Exception:
            # 指标端点保持可抓取，固定分类日志避免泄露依赖异常正文。
            logger.error("刷新Runtime指标失败")
        backlog_monitor = getattr(runtime, "outbox_backlog_monitor", None)
        if backlog_monitor is not None:
            try:
                backlog_report = await backlog_monitor.collect()
            except Exception:
                logger.error("刷新Outbox积压指标失败")
    application_metrics.update_runtime(component_statuses, worker_health)
    application_metrics.update_rca_consumer(rca_consumer_health)
    application_metrics.update_audit_retention(audit_retention_health)
    application_metrics.update_remediation_reclaim(remediation_reclaim_health)
    application_metrics.update_ticket_submission_consumer(
        ticket_submission_consumer_health
    )
    application_metrics.update_outbox_backlog(
        backlog_report,
        now=datetime.now(UTC),
    )
    return Response(
        content=generate_latest(application_metrics.registry),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
