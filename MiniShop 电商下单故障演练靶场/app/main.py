import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from app.agent_alerts import AgentAlertClient, AgentAlertClientConfig
from app.alertmanager import AlertmanagerAlertMapper, AlertmanagerRelay
from app.config import settings
from app.logging import configure_logging, log_event, reset_trace_id, set_trace_id
from app.metrics import metrics_response, observe_http_request
from app.routers import (
    alertmanager,
    checkout,
    faults,
    holdout,
    inventory,
    notification,
    payment,
)
from app.tracing import service_tracing

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        service_tracing.shutdown()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)
if settings.agent_url:
    app.state.alertmanager_relay = AlertmanagerRelay(
        AlertmanagerAlertMapper(settings.tenant_id),
        AgentAlertClient(
            AgentAlertClientConfig(
                agent_url=settings.agent_url,
                webhook_secret=settings.agent_webhook_secret,
                timeout_seconds=settings.agent_request_timeout_seconds,
            )
        ),
    )
else:
    app.state.alertmanager_relay = None


def service_name_for_path(path: str) -> str:
    if path.startswith("/checkout"):
        return "checkout-service"
    if path.startswith("/payment"):
        return "payment-service"
    if path.startswith("/inventory"):
        return "inventory-service"
    if path.startswith("/notification"):
        return "notification-service"
    if path.startswith("/faults"):
        return "fault-control-service"
    if path.startswith("/holdout"):
        return "holdout-fault-control-service"
    return "minishop-service"


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id") or f"trc_{uuid.uuid4().hex[:12]}"
    token = set_trace_id(trace_id)
    start = time.perf_counter()
    status_code = 500
    service_name = service_name_for_path(request.url.path)
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["x-trace-id"] = trace_id
        return response
    finally:
        duration = time.perf_counter() - start
        observe_http_request(
            service_name=service_name,
            endpoint=request.url.path,
            method=request.method,
            status_code=status_code,
            duration_seconds=duration,
        )
        log_event(
            level="ERROR" if status_code >= 500 else "INFO",
            service_name=service_name,
            endpoint=request.url.path,
            method=request.method,
            status_code=status_code,
            latency_ms=duration * 1000,
            error_code=f"HTTP_{status_code}" if status_code >= 400 else None,
            message="http request completed",
        )
        reset_trace_id(token)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error_code": "INTERNAL_SERVER_ERROR",
            "message": "unexpected MiniShop error",
            "path": request.url.path,
        },
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}


@app.get("/metrics")
async def metrics():
    return metrics_response()


app.include_router(checkout.router)
app.include_router(payment.router)
app.include_router(inventory.router)
app.include_router(notification.router)
app.include_router(faults.router)
app.include_router(holdout.router)
app.include_router(alertmanager.router)
