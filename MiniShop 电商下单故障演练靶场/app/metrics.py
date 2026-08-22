from typing import Iterable

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

from app.config import settings
from app.models import FaultRecord


HTTP_REQUESTS = Counter(
    "minishop_http_requests_total",
    "Total HTTP requests handled by MiniShop",
    ["service_name", "endpoint", "method", "status_code"],
)
HTTP_REQUEST_DURATION = Histogram(
    "minishop_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["service_name", "endpoint", "method"],
)
HTTP_REQUEST_ERRORS = Counter(
    "minishop_http_request_errors_total",
    "Total HTTP errors handled by MiniShop",
    ["service_name", "endpoint", "error_code"],
)
FAULT_ENABLED = Gauge(
    "minishop_fault_enabled",
    "Whether a MiniShop fault is currently enabled",
    ["service_name", "fault_type"],
)
HOLDOUT_FAULT_ENABLED = Gauge(
    "minishop_holdout_fault_enabled",
    "Whether a hidden-holdout fault is currently enabled",
    ["service_name", "fault_type"],
)
HOLDOUT_REQUEST_TOTAL = Counter(
    "minishop_holdout_request_total",
    "Hidden-holdout requests handled by the fault lab",
    ["service_name", "fault_type"],
)
PAYMENT_ERROR_TOTAL = Counter(
    "minishop_payment_error_total",
    "Payment service injected errors",
)
PAYMENT_DEPLOYMENT_REGRESSION_TOTAL = Counter(
    "minishop_payment_deployment_regression_total",
    "Payment service errors caused by the deployment regression fault",
)
INVENTORY_DB_TIMEOUT_TOTAL = Counter(
    "minishop_inventory_db_timeout_total",
    "Inventory service injected database timeouts",
)
DOWNSTREAM_TIMEOUT_TOTAL = Counter(
    "minishop_downstream_timeout_total",
    "Checkout downstream timeout or failure count",
    ["downstream_service"],
)
PLATFORM_HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Platform-compatible MiniShop HTTP request count",
    ["tenant_id", "service", "endpoint", "method", "status"],
)
PLATFORM_HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Platform-compatible MiniShop HTTP request duration",
    ["tenant_id", "service", "endpoint", "method"],
)
SERVICE_UP = Gauge(
    "minishop_service_up",
    "Whether a logical MiniShop service is available",
    ["tenant_id", "service"],
)

for _service_name in (
    "checkout-service",
    "payment-service",
    "inventory-service",
    "notification-service",
):
    SERVICE_UP.labels(settings.tenant_id, _service_name).set(1)


def ensure_service_up(service_name: str) -> None:
    SERVICE_UP.labels(settings.tenant_id, service_name).set(1)


def observe_http_request(
    *,
    service_name: str,
    endpoint: str,
    method: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    HTTP_REQUESTS.labels(service_name, endpoint, method, str(status_code)).inc()
    HTTP_REQUEST_DURATION.labels(service_name, endpoint, method).observe(duration_seconds)
    PLATFORM_HTTP_REQUESTS.labels(
        settings.tenant_id,
        service_name,
        endpoint,
        method,
        str(status_code),
    ).inc()
    PLATFORM_HTTP_REQUEST_DURATION.labels(
        settings.tenant_id,
        service_name,
        endpoint,
        method,
    ).observe(duration_seconds)
    if status_code >= 400:
        HTTP_REQUEST_ERRORS.labels(service_name, endpoint, f"HTTP_{status_code}").inc()


def set_fault_enabled(service_name: str, fault_type: str, enabled: bool) -> None:
    FAULT_ENABLED.labels(service_name, fault_type).set(1 if enabled else 0)


def clear_fault_gauges(records: Iterable[FaultRecord]) -> None:
    for record in records:
        set_fault_enabled(record.service_name, record.fault_type, False)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
