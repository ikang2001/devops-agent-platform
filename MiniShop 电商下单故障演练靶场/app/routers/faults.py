from typing import Optional

from fastapi import APIRouter

from app.faults import fault_state
from app.metrics import clear_fault_gauges, set_fault_enabled
from app.models import FaultControlRequest

router = APIRouter()


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
    set_fault_enabled(record.service_name, record.fault_type, True)
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
    set_fault_enabled(record.service_name, record.fault_type, True)
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
    set_fault_enabled(record.service_name, record.fault_type, True)
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
    set_fault_enabled(record.service_name, record.fault_type, True)
    return {"fault": record}


@router.post("/faults/reset")
async def reset_faults():
    records = fault_state.reset()
    clear_fault_gauges(records)
    return {"reset": True, "cleared": len(records)}
