import asyncio
import time
import uuid
from typing import Dict

from fastapi import HTTPException
from opentelemetry.trace import Status, StatusCode

from app.config import settings
from app.faults import fault_state
from app.logging import get_trace_id, log_event
from app.metrics import (
    DOWNSTREAM_TIMEOUT_TOTAL,
    INVENTORY_DB_TIMEOUT_TOTAL,
    PAYMENT_ERROR_TOTAL,
)
from app.models import (
    CheckoutRequest,
    InventoryRequest,
    NotificationRequest,
    PaymentRequest,
)
from app.tracing import service_tracing


async def reserve_inventory(request: InventoryRequest) -> Dict[str, object]:
    tracer = service_tracing.tracer("inventory-service")
    with tracer.start_as_current_span("inventory.reserve") as span:
        span.set_attribute("minishop.inventory.sku", request.sku)
        span.set_attribute("minishop.inventory.quantity", request.quantity)
        delay_ms = fault_state.delay_ms("inventory-service", "db_timeout")
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
            INVENTORY_DB_TIMEOUT_TOTAL.inc()
            span.set_attribute("minishop.fault.type", "db_timeout")
            span.set_attribute("error.type", "DB_TIMEOUT")
            span.set_status(Status(StatusCode.ERROR, "DB_TIMEOUT"))
            log_event(
                level="ERROR",
                service_name="inventory-service",
                endpoint="/inventory/reserve",
                status_code=503,
                latency_ms=delay_ms,
                error_code="DB_TIMEOUT",
                fault_type="db_timeout",
                message="inventory database query timeout",
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "DB_TIMEOUT",
                    "message": "inventory database query timeout",
                    "trace_id": get_trace_id(),
                },
            )

        await asyncio.sleep(0.01)
        return {
            "sku": request.sku,
            "reserved_quantity": request.quantity,
            "reservation_id": f"rsv_{uuid.uuid4().hex[:10]}",
        }


async def pay(request: PaymentRequest) -> Dict[str, object]:
    tracer = service_tracing.tracer("payment-service")
    with tracer.start_as_current_span("payment.pay") as span:
        span.set_attribute("minishop.order.id", request.order_id)
        span.set_attribute("minishop.payment.currency", request.currency)
        if fault_state.should_fail("payment-service", "payment_error"):
            PAYMENT_ERROR_TOTAL.inc()
            span.set_attribute("minishop.fault.type", "payment_error")
            span.set_attribute("error.type", "PAYMENT_GATEWAY_ERROR")
            span.set_status(Status(StatusCode.ERROR, "PAYMENT_GATEWAY_ERROR"))
            log_event(
                level="ERROR",
                service_name="payment-service",
                endpoint="/payment/pay",
                status_code=500,
                error_code="PAYMENT_GATEWAY_ERROR",
                fault_type="payment_error",
                message="payment gateway returned 500",
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error_code": "PAYMENT_GATEWAY_ERROR",
                    "message": "payment gateway returned 500",
                    "trace_id": get_trace_id(),
                },
            )

        await asyncio.sleep(0.01)
        return {
            "payment_id": f"pay_{uuid.uuid4().hex[:10]}",
            "order_id": request.order_id,
            "amount": request.amount,
            "currency": request.currency,
            "status": "paid",
        }


async def send_notification(request: NotificationRequest) -> Dict[str, object]:
    tracer = service_tracing.tracer("notification-service")
    with tracer.start_as_current_span("notification.send") as span:
        span.set_attribute("minishop.order.id", request.order_id)
        span.set_attribute("minishop.notification.channel", request.channel)
        await asyncio.sleep(0.005)
        return {
            "notification_id": f"ntf_{uuid.uuid4().hex[:10]}",
            "order_id": request.order_id,
            "channel": request.channel,
            "status": "sent",
        }


async def checkout(request: CheckoutRequest) -> Dict[str, object]:
    tracer = service_tracing.tracer("checkout-service")
    with tracer.start_as_current_span("checkout.process") as span:
        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        span.set_attribute("minishop.order.id", order_id)
        span.set_attribute("minishop.checkout.item_count", len(request.items))
        timings: Dict[str, float] = {}

        checkout_delay_ms = fault_state.delay_ms("checkout-service", "latency")
        if checkout_delay_ms:
            span.set_attribute("minishop.fault.type", "latency")
            span.set_attribute("minishop.checkout.injected_delay_ms", checkout_delay_ms)
            start = time.perf_counter()
            await asyncio.sleep(checkout_delay_ms / 1000)
            timings["checkout_self_ms"] = round((time.perf_counter() - start) * 1000, 2)

        inventory_start = time.perf_counter()
        try:
            for item in request.items:
                await reserve_inventory(InventoryRequest(sku=item.sku, quantity=item.quantity))
        except HTTPException as exc:
            timings["inventory_ms"] = round((time.perf_counter() - inventory_start) * 1000, 2)
            DOWNSTREAM_TIMEOUT_TOTAL.labels("inventory-service").inc()
            log_event(
                level="ERROR",
                service_name="checkout-service",
                endpoint="/checkout",
                status_code=503,
                latency_ms=timings["inventory_ms"],
                error_code="CHECKOUT_DOWNSTREAM_FAILURE",
                fault_type="inventory_db_timeout",
                message="checkout failed because inventory downstream failed",
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "CHECKOUT_DOWNSTREAM_FAILURE",
                    "message": "checkout failed because inventory downstream failed",
                    "downstream": "inventory-service",
                    "downstream_detail": exc.detail,
                    "trace_id": get_trace_id(),
                },
            ) from exc
        timings["inventory_ms"] = round((time.perf_counter() - inventory_start) * 1000, 2)

        amount = sum(item.quantity * settings.item_unit_price for item in request.items)
        payment_start = time.perf_counter()
        payment_result = await pay(
            PaymentRequest(order_id=order_id, amount=amount, currency=settings.default_currency)
        )
        timings["payment_ms"] = round((time.perf_counter() - payment_start) * 1000, 2)

        notification_result = None
        notification_start = time.perf_counter()
        try:
            notification_result = await send_notification(
                NotificationRequest(user_id=request.user_id, order_id=order_id)
            )
        except HTTPException:
            if not settings.notification_best_effort:
                raise
        timings["notification_ms"] = round((time.perf_counter() - notification_start) * 1000, 2)

        return {
            "order_id": order_id,
            "status": "created",
            "trace_id": get_trace_id(),
            "amount": amount,
            "currency": settings.default_currency,
            "payment": payment_result,
            "notification": notification_result,
            "timings_ms": timings,
        }
