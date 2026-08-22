"""Operational controls for the hidden-holdout fault lab.

This module intentionally contains only executable fault controls and service
names. It does not load scenario manifests or ground-truth labels.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock

from app.logging import log_event
from app.metrics import (
    HOLDOUT_FAULT_ENABLED,
    HOLDOUT_REQUEST_TOTAL,
    ensure_service_up,
    observe_http_request,
)
from app.tracing import service_tracing


@dataclass(frozen=True)
class HoldoutFaultSpec:
    service_name: str
    fault_type: str
    resource_name: str
    expected_status: int
    summary: str


HOLDOUT_FAULTS: dict[str, HoldoutFaultSpec] = {
    "cross-zone-network-alert-no-impact": HoldoutFaultSpec(
        "shipping-quote-api",
        "network_alert_no_impact",
        "cross-zone-link",
        200,
        "Cross-zone network alert without customer impact",
    ),
    "dns-resolution-failure": HoldoutFaultSpec(
        "pricing-api", "dns_failure", "pricing.internal", 503, "DNS resolution failure"
    ),
    "elasticsearch-query-latency": HoldoutFaultSpec(
        "catalog-search-api",
        "query_latency",
        "elasticsearch-hot-tier",
        503,
        "Elasticsearch query latency with distractors",
    ),
    "memory-leak-gc-pause": HoldoutFaultSpec(
        "search-api", "gc_pause", "search-api-memory", 503, "Memory leak and GC pause"
    ),
    "mongodb-connection-timeout": HoldoutFaultSpec(
        "profile-api",
        "connection_timeout",
        "mongodb-primary",
        503,
        "MongoDB connection timeout",
    ),
    "mysql-lock-contention": HoldoutFaultSpec(
        "cart-api", "lock_contention", "mysql-primary", 503, "MySQL lock contention"
    ),
    "object-storage-throttling": HoldoutFaultSpec(
        "invoice-api",
        "storage_throttling",
        "s3://invoice-bucket",
        503,
        "Object storage throttling",
    ),
    "rabbitmq-consumer-backlog": HoldoutFaultSpec(
        "notification-worker",
        "consumer_backlog",
        "rabbitmq-events",
        503,
        "RabbitMQ consumer backlog",
    ),
    "service-mesh-retry-storm": HoldoutFaultSpec(
        "recommendation-api",
        "retry_storm",
        "mesh-egress",
        503,
        "Service mesh retry storm",
    ),
    "third-party-sms-timeout": HoldoutFaultSpec(
        "notification-api",
        "vendor_timeout",
        "sms-provider",
        503,
        "Third-party SMS timeout",
    ),
    "postgresql-deadlock-chain": HoldoutFaultSpec(
        "basket-worker",
        "lock_contention",
        "orders-db-primary",
        503,
        "PostgreSQL deadlock chain",
    ),
    "cassandra-session-deadline": HoldoutFaultSpec(
        "account-profile-worker",
        "connection_timeout",
        "cassandra-ring-a",
        503,
        "Cassandra session deadline",
    ),
    "nats-delivery-lag": HoldoutFaultSpec(
        "email-dispatch-worker",
        "consumer_backlog",
        "nats-notifications",
        503,
        "NATS delivery lag",
    ),
    "service-discovery-lookup-deadline": HoldoutFaultSpec(
        "promotion-engine-api",
        "dns_failure",
        "discovery.internal",
        503,
        "Service discovery lookup deadline",
    ),
    "sidecar-retry-amplification": HoldoutFaultSpec(
        "personalization-worker",
        "retry_storm",
        "service-mesh-gateway",
        503,
        "Sidecar retry amplification",
    ),
    "blob-store-rate-limit": HoldoutFaultSpec(
        "receipt-generator-api",
        "storage_throttling",
        "blob-receipts-container",
        503,
        "Blob store rate limit",
    ),
    "tax-provider-response-deadline": HoldoutFaultSpec(
        "tax-calculator-worker",
        "vendor_timeout",
        "tax-vendor-edge",
        503,
        "Tax provider response deadline",
    ),
    "heap-thrashing-pause": HoldoutFaultSpec(
        "product-indexer-worker",
        "gc_pause",
        "indexer-heap-region",
        503,
        "Heap thrashing pause",
    ),
    "opensearch-shard-latency": HoldoutFaultSpec(
        "product-discovery-worker",
        "query_latency",
        "opensearch-warm-tier",
        503,
        "OpenSearch shard latency",
    ),
    "inter-region-link-alert-no-impact": HoldoutFaultSpec(
        "fulfillment-router-worker",
        "network_alert_no_impact",
        "inter-region-link",
        200,
        "Inter-region link alert without customer impact",
    ),
}


class HoldoutFaultState:
    def __init__(self) -> None:
        self._lock = RLock()
        self._active: dict[str, float] = {}

    def enable(self, fault_name: str, duration_seconds: int) -> HoldoutFaultSpec:
        spec = HOLDOUT_FAULTS[fault_name]
        ensure_service_up(spec.service_name)
        with self._lock:
            self._active[fault_name] = time.monotonic() + duration_seconds
        HOLDOUT_FAULT_ENABLED.labels(spec.service_name, spec.fault_type).set(1)
        return spec

    def active(self) -> tuple[str, HoldoutFaultSpec] | None:
        with self._lock:
            for fault_name, expires_at in tuple(self._active.items()):
                spec = HOLDOUT_FAULTS[fault_name]
                if expires_at <= time.monotonic():
                    self._active.pop(fault_name, None)
                    HOLDOUT_FAULT_ENABLED.labels(
                        spec.service_name, spec.fault_type
                    ).set(0)
                    continue
                return fault_name, spec
        return None

    def reset(self) -> int:
        with self._lock:
            active = tuple(self._active)
            self._active.clear()
        for fault_name in active:
            spec = HOLDOUT_FAULTS[fault_name]
            HOLDOUT_FAULT_ENABLED.labels(spec.service_name, spec.fault_type).set(0)
        return len(active)


holdout_fault_state = HoldoutFaultState()


def service_for_path(path: str) -> str | None:
    active = holdout_fault_state.active()
    if active is None or not path.startswith("/holdout/"):
        return None
    return active[1].service_name


def execute_request(path: str) -> tuple[int, dict[str, object]]:
    active = holdout_fault_state.active()
    if active is None:
        return 503, {"error_code": "HOLDOUT_FAULT_NOT_ENABLED"}
    fault_name, spec = active
    HOLDOUT_REQUEST_TOTAL.labels(spec.service_name, spec.fault_type).inc()
    tracer = service_tracing.tracer(spec.service_name)
    with tracer.start_as_current_span("holdout.request") as span:
        span.set_attribute("minishop.holdout.fault", fault_name)
        span.set_attribute("minishop.holdout.service", spec.service_name)
        span.set_attribute("minishop.holdout.status", spec.expected_status)
        span.set_attribute("peer.service", spec.resource_name)
        message = (
            f"{spec.resource_name} emitted a transient signal without customer impact"
            if spec.expected_status == 200
            else f"request to {spec.resource_name} failed after a bounded dependency budget"
        )
        log_event(
            level="INFO" if spec.expected_status == 200 else "ERROR",
            service_name=spec.service_name,
            endpoint=path,
            status_code=spec.expected_status,
            fault_type=spec.fault_type,
            message=message,
        )
        observe_http_request(
            service_name=spec.service_name,
            endpoint="/internal/dependency",
            method="POST",
            status_code=spec.expected_status,
            duration_seconds=0.02,
        )
        return spec.expected_status, {
            "service_name": spec.service_name,
            "fault_type": spec.fault_type,
            "message": message,
        }
