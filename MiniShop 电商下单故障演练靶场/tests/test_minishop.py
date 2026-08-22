import json
import logging
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


def test_generic_fault_emits_structured_control_plane_evidence(caplog):
    with caplog.at_level(logging.WARNING, logger="minishop"):
        response = client.post(
            "/faults/third-party-api-timeout",
            json={"duration_seconds": 60, "created_by": "test"},
        )

    assert response.status_code == 200
    events = [json.loads(record.message) for record in caplog.records]
    event = next(
        item
        for item in events
        if item.get("fault_type") == "third_party_api_timeout"
    )
    assert event["service_name"] == "payment-service"
    assert event["error.type"] == "provider_timeout"
    assert event["resource.name"] == "payment-provider"


def test_hidden_holdout_fault_control_exposes_only_operational_data():
    response = client.post(
        "/holdout/faults/dns-resolution-failure",
        json={"duration_seconds": 60, "created_by": "blackbox"},
    )

    assert response.status_code == 200
    body = response.json()["fault"]
    assert body["service_name"] == "pricing-api"
    assert "root_cause" not in body
    assert "ground_truth" not in body

    trigger = client.post("/holdout/checkout-gateway/request")
    assert trigger.status_code == 503
    assert trigger.json()["fault_type"] == "dns_failure"
    assert "minishop_holdout_fault_enabled" in client.get("/metrics").text


def test_hidden_holdout_no_impact_keeps_trigger_successful():
    client.post(
        "/holdout/faults/cross-zone-network-alert-no-impact",
        json={"duration_seconds": 60, "created_by": "blackbox"},
    )

    response = client.post("/holdout/checkout-gateway/request")

    assert response.status_code == 200
    assert response.json()["service_name"] == "shipping-quote-api"


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
