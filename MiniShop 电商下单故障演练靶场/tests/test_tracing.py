import asyncio
from typing import Iterator, List, Tuple

import pytest
from fastapi import HTTPException
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import app.services as services
from app.faults import fault_state
from app.models import CheckoutItem, CheckoutRequest, InventoryRequest, PaymentRequest
from app.tracing import SERVICE_NAMES, ServiceTracing, build_service_tracing


TracingFixture = Tuple[ServiceTracing, List[InMemorySpanExporter], List[str]]


@pytest.fixture(autouse=True)
def reset_faults() -> Iterator[None]:
    fault_state.reset()
    yield
    fault_state.reset()


@pytest.fixture
def in_memory_tracing(monkeypatch: pytest.MonkeyPatch) -> Iterator[TracingFixture]:
    exporters: List[InMemorySpanExporter] = []
    endpoints: List[str] = []

    def exporter_factory(endpoint: str) -> InMemorySpanExporter:
        endpoints.append(endpoint)
        exporter = InMemorySpanExporter()
        exporters.append(exporter)
        return exporter

    tracing = build_service_tracing(
        "http://collector:4318/v1/traces",
        exporter_factory=exporter_factory,
        processor_factory=SimpleSpanProcessor,
    )
    monkeypatch.setattr(services, "service_tracing", tracing)
    try:
        yield tracing, exporters, endpoints
    finally:
        tracing.shutdown()


def finished_spans(exporters: List[InMemorySpanExporter]) -> List[ReadableSpan]:
    return [span for exporter in exporters for span in exporter.get_finished_spans()]


def test_blank_endpoint_uses_noop_tracers_without_constructing_exporters() -> None:
    def unexpected_exporter(endpoint: str) -> InMemorySpanExporter:
        raise AssertionError(f"unexpected exporter for {endpoint}")

    tracing = build_service_tracing("  ", exporter_factory=unexpected_exporter)

    assert tracing.enabled is False
    for service_name in SERVICE_NAMES:
        with tracing.tracer(service_name).start_as_current_span("disabled") as span:
            assert span.is_recording() is False


def test_checkout_creates_cross_service_trace_with_service_resources(
    in_memory_tracing: TracingFixture,
) -> None:
    tracing, exporters, endpoints = in_memory_tracing

    result = asyncio.run(
        services.checkout(
            CheckoutRequest(
                user_id="trace-user",
                items=[CheckoutItem(sku="sku-trace", quantity=1)],
            )
        )
    )

    assert result["status"] == "created"
    assert tracing.enabled is True
    assert endpoints == ["http://collector:4318/v1/traces"] * len(SERVICE_NAMES)
    spans = finished_spans(exporters)
    spans_by_name = {span.name: span for span in spans}
    assert set(spans_by_name) == {
        "checkout.process",
        "inventory.reserve",
        "payment.pay",
        "notification.send",
    }
    assert {
        span.resource.attributes["service.name"] for span in spans
    } == set(SERVICE_NAMES)

    checkout_span = spans_by_name["checkout.process"]
    assert checkout_span.parent is None
    assert checkout_span.context is not None
    for child_name in ("inventory.reserve", "payment.pay", "notification.send"):
        child_span = spans_by_name[child_name]
        assert child_span.parent is not None
        assert child_span.context is not None
        assert child_span.parent.span_id == checkout_span.context.span_id
        assert child_span.context.trace_id == checkout_span.context.trace_id


def test_inventory_timeout_marks_inventory_span_error(
    in_memory_tracing: TracingFixture,
) -> None:
    _, exporters, _ = in_memory_tracing
    fault_state.enable(
        service_name="inventory-service",
        fault_type="db_timeout",
        error_rate=1.0,
        delay_ms=1,
        duration_seconds=60,
        created_by="trace-test",
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(services.reserve_inventory(InventoryRequest(sku="sku-1", quantity=1)))

    assert error.value.status_code == 503
    span = next(span for span in finished_spans(exporters) if span.name == "inventory.reserve")
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["minishop.fault.type"] == "db_timeout"
    assert span.attributes["error.type"] == "DB_TIMEOUT"


def test_payment_error_marks_payment_span_error(
    in_memory_tracing: TracingFixture,
) -> None:
    _, exporters, _ = in_memory_tracing
    fault_state.enable(
        service_name="payment-service",
        fault_type="payment_error",
        error_rate=1.0,
        delay_ms=0,
        duration_seconds=60,
        created_by="trace-test",
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            services.pay(PaymentRequest(order_id="ord-trace", amount=10.0, currency="CNY"))
        )

    assert error.value.status_code == 500
    span = next(span for span in finished_spans(exporters) if span.name == "payment.pay")
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["minishop.fault.type"] == "payment_error"
    assert span.attributes["error.type"] == "PAYMENT_GATEWAY_ERROR"
