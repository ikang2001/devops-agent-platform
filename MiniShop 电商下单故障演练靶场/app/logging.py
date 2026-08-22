import contextvars
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from app.config import settings


trace_id_var = contextvars.ContextVar("trace_id", default="")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def set_trace_id(trace_id: str):
    return trace_id_var.set(trace_id)


def reset_trace_id(token) -> None:
    trace_id_var.reset(token)


def get_trace_id() -> str:
    return trace_id_var.get()


def log_event(
    *,
    level: str,
    service_name: str,
    message: str,
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    latency_ms: Optional[float] = None,
    error_code: Optional[str] = None,
    fault_type: Optional[str] = None,
    error_type: Optional[str] = None,
    resource_name: Optional[str] = None,
) -> None:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant_id": settings.tenant_id,
        "level": level.upper(),
        "service_name": service_name,
        "trace_id": get_trace_id(),
        "endpoint": endpoint,
        "method": method,
        "status_code": status_code,
        "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
        "error_code": error_code,
        "fault_type": fault_type,
        "error.type": error_type,
        "resource.name": resource_name,
        "message": message,
    }
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger("minishop").log(log_level, json.dumps(event, ensure_ascii=False))
