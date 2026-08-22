from typing import Optional

from fastapi import APIRouter, HTTPException

from app.faults import fault_evidence_metadata, fault_state
from app.holdout import holdout_fault_state
from app.logging import log_event
from app.metrics import clear_fault_gauges, set_fault_enabled
from app.models import FaultControlRequest, FaultRecord

router = APIRouter()


def _publish_fault_observation(record: FaultRecord, endpoint: str) -> None:
    metadata = fault_evidence_metadata(record.fault_type)
    set_fault_enabled(record.service_name, record.fault_type, True)
    log_event(
        level="WARNING",
        service_name=record.service_name,
        endpoint=endpoint,
        method="POST",
        status_code=200,
        fault_type=record.fault_type,
        error_type=metadata.error_type,
        resource_name=metadata.resource_name,
        message="fault injection control-plane event is active",
    )


@router.get("/faults")
async def list_faults():
    return {"faults": fault_state.list_active()}


@router.post("/faults/payment-error")
async def enable_payment_error(request: Optional[FaultControlRequest] = None):
    payload = request or FaultControlRequest()
    record = fault_state.enable(
        service_name="payment-service",
        fault_type="payment_error",
        error_rate=payload.error_rate,
        delay_ms=0,
        duration_seconds=payload.duration_seconds,
        created_by=payload.created_by,
    )
    _publish_fault_observation(record, "/faults/payment-error")
    return {"fault": record}


@router.post("/faults/deployment-regression")
async def enable_deployment_regression(
    request: Optional[FaultControlRequest] = None,
):
    payload = request or FaultControlRequest()
    record = fault_state.enable(
        service_name="payment-service",
        fault_type="deployment_regression",
        error_rate=payload.error_rate,
        delay_ms=0,
        duration_seconds=payload.duration_seconds,
        created_by=payload.created_by,
    )
    _publish_fault_observation(record, "/faults/deployment-regression")
    return {"fault": record}


@router.post("/faults/inventory-db-timeout")
async def enable_inventory_db_timeout(request: Optional[FaultControlRequest] = None):
    payload = request or FaultControlRequest()
    record = fault_state.enable(
        service_name="inventory-service",
        fault_type="db_timeout",
        error_rate=1.0,
        delay_ms=payload.delay_ms,
        duration_seconds=payload.duration_seconds,
        created_by=payload.created_by,
    )
    _publish_fault_observation(record, "/faults/inventory-db-timeout")
    return {"fault": record}


@router.post("/faults/checkout-latency")
async def enable_checkout_latency(request: Optional[FaultControlRequest] = None):
    payload = request or FaultControlRequest()
    record = fault_state.enable(
        service_name="checkout-service",
        fault_type="latency",
        error_rate=1.0,
        delay_ms=payload.delay_ms,
        duration_seconds=payload.duration_seconds,
        created_by=payload.created_by,
    )
    _publish_fault_observation(record, "/faults/checkout-latency")
    return {"fault": record}


@router.post("/faults/reset")
async def reset_faults():
    records = fault_state.reset()
    clear_fault_gauges(records)
    return {
        "reset": True,
        "cleared": len(records),
        "holdout_cleared": holdout_fault_state.reset(),
    }


_GENERIC_FAULTS = {
    "config-regression": ("payment-service", "config_regression"),
    "redis-latency": ("inventory-service", "redis_latency"),
    "connection-pool-exhaustion": ("checkout-service", "connection_pool_exhaustion"),
    "third-party-api-timeout": ("payment-service", "third_party_api_timeout"),
    "cascading-failure": ("checkout-service", "cascading_failure"),
    "known-error-repeat": ("payment-service", "known_error_repeat"),
    "misleading-history": ("payment-service", "misleading_history"),
    "false-positive-alert": ("checkout-service", "false_positive_alert"),
    "cpu-saturation": ("checkout-service", "cpu_saturation"),
    "memory-pressure": ("checkout-service", "memory_pressure"),
}


@router.post("/faults/{fault_name}")
async def enable_generic_fault(
    fault_name: str,
    request: Optional[FaultControlRequest] = None,
):
    target = _GENERIC_FAULTS.get(fault_name)
    if target is None:
        raise HTTPException(status_code=404, detail="unknown fault scenario")
    payload = request or FaultControlRequest()
    service_name, fault_type = target
    record = fault_state.enable(
        service_name=service_name,
        fault_type=fault_type,
        error_rate=payload.error_rate,
        delay_ms=payload.delay_ms,
        duration_seconds=payload.duration_seconds,
        created_by=payload.created_by,
    )
    _publish_fault_observation(record, f"/faults/{fault_name}")
    return {"fault": record}
