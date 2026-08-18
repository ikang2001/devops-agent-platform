import time

from fastapi.testclient import TestClient

from app.main import app
from scripts.send_agent_alert import build_alert_payload


client = TestClient(app)


def setup_function():
    client.post("/faults/reset")


def test_checkout_success_returns_order_and_trace_id():
    response = client.post(
        "/checkout",
        json={
            "user_id": "u1001",
            "items": [{"sku": "sku-001", "quantity": 1}],
            "idempotency_key": "test-001",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["order_id"].startswith("ord_")
    assert body["trace_id"]
    assert body["payment"]["status"] == "paid"


def test_payment_error_fault_returns_500():
    client.post(
        "/faults/payment-error",
        json={"error_rate": 1.0, "duration_seconds": 60, "created_by": "test"},
    )

    response = client.post(
        "/payment/pay",
        json={"order_id": "ord-test", "amount": 10.0, "currency": "CNY"},
    )

    assert response.status_code == 500
    assert response.json()["detail"]["error_code"] == "PAYMENT_GATEWAY_ERROR"


def test_deployment_regression_fault_returns_distinct_error_and_metric():
    client.post(
        "/faults/deployment-regression",
        json={"error_rate": 1.0, "duration_seconds": 60, "created_by": "test"},
    )

    response = client.post(
        "/payment/pay",
        json={"order_id": "ord-deploy", "amount": 10.0, "currency": "CNY"},
    )

    assert response.status_code == 500
    assert response.json()["detail"]["error_code"] == ("PAYMENT_DEPLOYMENT_REGRESSION")
    assert "minishop_payment_deployment_regression_total" in (client.get("/metrics").text)


def test_inventory_db_timeout_makes_checkout_fail_and_metric_exist():
    client.post(
        "/faults/inventory-db-timeout",
        json={"delay_ms": 20, "duration_seconds": 60, "created_by": "test"},
    )

    response = client.post(
        "/checkout",
        json={"user_id": "u1001", "items": [{"sku": "sku-001", "quantity": 1}]},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "CHECKOUT_DOWNSTREAM_FAILURE"

    metrics = client.get("/metrics").text
    assert "minishop_inventory_db_timeout_total" in metrics


def test_checkout_latency_fault_delays_response():
    client.post(
        "/faults/checkout-latency",
        json={"delay_ms": 80, "duration_seconds": 60, "created_by": "test"},
    )

    start = time.perf_counter()
    response = client.post(
        "/checkout",
        json={"user_id": "u1001", "items": [{"sku": "sku-001", "quantity": 1}]},
    )
    elapsed = time.perf_counter() - start

    assert response.status_code == 200
    assert elapsed >= 0.06


def test_reset_clears_faults():
    client.post("/faults/payment-error", json={"error_rate": 1.0})
    assert client.get("/faults").json()["faults"]

    response = client.post("/faults/reset")

    assert response.status_code == 200
    assert client.get("/faults").json()["faults"] == []


def test_metrics_exposes_required_names():
    text = client.get("/metrics").text

    assert "minishop_http_requests_total" in text
    assert "minishop_http_request_duration_seconds" in text
    assert "minishop_http_request_errors_total" in text
    assert "minishop_fault_enabled" in text
    assert "minishop_payment_error_total" in text
    assert "minishop_inventory_db_timeout_total" in text
    assert "http_requests_total" in text
    assert "http_request_duration_seconds" in text
    assert "minishop_service_up" in text


def test_agent_alert_payload_uses_external_event_id():
    payload = build_alert_payload()

    assert payload["tenant_id"] == "demo"
    assert payload["source"] == "alertmanager"
    assert payload["service_name"] == "checkout-service"
    assert payload["severity"] == "CRITICAL"
    assert payload["summary"]
    assert payload["starts_at"]
    assert payload["external_event_id"] == "checkout-high-latency-001"
    assert payload["fingerprint"] == "minishop:checkout-service:high-latency"
