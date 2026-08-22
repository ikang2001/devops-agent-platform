"""Generate the versioned v0.7 hidden-holdout public/private fixture set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HOLDOUTS = (
    {
        "id": "mysql-lock-contention",
        "title": "MySQL lock contention",
        "service": "cart-api",
        "fault": "lock_contention",
        "resource": "mysql-primary",
        "root_type": "resource_exhaustion",
        "entry": "checkout-gateway",
        "topology": "single-zone",
        "required": ["METRIC", "LOG", "TRACE"],
        "tools": ["metrics.query@v1", "logs.query@v1", "traces.query@v1"],
        "chain": [("mysql-primary", "cart-api"), ("cart-api", "checkout-gateway")],
        "affected": ["cart-api", "checkout-gateway"],
        "error": "transaction waited on a row lock beyond the deadline",
    },
    {
        "id": "mongodb-connection-timeout",
        "title": "MongoDB connection timeout",
        "service": "profile-api",
        "fault": "connection_timeout",
        "resource": "mongodb-primary",
        "root_type": "dependency_timeout",
        "entry": "checkout-gateway",
        "topology": "single-zone",
        "required": ["METRIC", "LOG", "TRACE"],
        "tools": ["metrics.query@v1", "logs.query@v1", "traces.query@v1"],
        "chain": [
            ("mongodb-primary", "profile-api"),
            ("profile-api", "checkout-gateway"),
        ],
        "affected": ["profile-api", "checkout-gateway"],
        "error": "profile storage could not establish a session before deadline",
    },
    {
        "id": "rabbitmq-consumer-backlog",
        "title": "RabbitMQ consumer backlog",
        "service": "notification-worker",
        "fault": "consumer_backlog",
        "resource": "rabbitmq-events",
        "root_type": "resource_exhaustion",
        "entry": "order-api",
        "topology": "cross-zone",
        "required": ["METRIC", "LOG"],
        "tools": ["metrics.query@v1", "logs.query@v1"],
        "chain": [
            ("rabbitmq-events", "notification-worker"),
            ("notification-worker", "order-api"),
        ],
        "affected": ["notification-worker", "order-api"],
        "error": "consumer lag increased while delivery acknowledgements stalled",
    },
    {
        "id": "dns-resolution-failure",
        "title": "DNS resolution failure",
        "service": "pricing-api",
        "fault": "dns_failure",
        "resource": "pricing.internal",
        "root_type": "dependency_timeout",
        "entry": "checkout-gateway",
        "topology": "cross-zone",
        "required": ["LOG", "TRACE"],
        "tools": ["logs.query@v1", "traces.query@v1"],
        "chain": [
            ("pricing.internal", "pricing-api"),
            ("pricing-api", "checkout-gateway"),
        ],
        "affected": ["pricing-api", "checkout-gateway"],
        "error": "resolver returned SERVFAIL for the pricing endpoint",
    },
    {
        "id": "service-mesh-retry-storm",
        "title": "Service mesh retry storm",
        "service": "recommendation-api",
        "fault": "retry_storm",
        "resource": "mesh-egress",
        "root_type": "resource_exhaustion",
        "entry": "checkout-gateway",
        "topology": "mesh-egress",
        "required": ["METRIC", "LOG", "TOPOLOGY"],
        "tools": ["metrics.query@v1", "logs.query@v1", "topology.query@v1"],
        "chain": [
            ("mesh-egress", "recommendation-api"),
            ("recommendation-api", "checkout-gateway"),
        ],
        "affected": ["recommendation-api", "checkout-gateway"],
        "error": "proxy retry budget was exhausted for the upstream route",
    },
    {
        "id": "object-storage-throttling",
        "title": "Object storage throttling",
        "service": "invoice-api",
        "fault": "storage_throttling",
        "resource": "s3://invoice-bucket",
        "root_type": "resource_exhaustion",
        "entry": "order-api",
        "topology": "cross-zone",
        "required": ["METRIC", "LOG", "TRACE"],
        "tools": ["metrics.query@v1", "logs.query@v1", "traces.query@v1"],
        "chain": [("s3://invoice-bucket", "invoice-api"), ("invoice-api", "order-api")],
        "affected": ["invoice-api", "order-api"],
        "error": "object storage rejected the request with a throttling response",
    },
    {
        "id": "third-party-sms-timeout",
        "title": "Third-party SMS timeout",
        "service": "notification-api",
        "fault": "vendor_timeout",
        "resource": "sms-provider",
        "root_type": "dependency_timeout",
        "entry": "order-api",
        "topology": "single-zone",
        "required": ["LOG", "TRACE"],
        "tools": ["logs.query@v1", "traces.query@v1"],
        "chain": [
            ("sms-provider", "notification-api"),
            ("notification-api", "order-api"),
        ],
        "affected": ["notification-api", "order-api"],
        "error": (
            "vendor acknowledgement did not arrive before the request budget "
            "expired"
        ),
    },
    {
        "id": "memory-leak-gc-pause",
        "title": "Memory leak and GC pause",
        "service": "search-api",
        "fault": "gc_pause",
        "resource": "search-api-memory",
        "root_type": "resource_exhaustion",
        "entry": "checkout-gateway",
        "topology": "single-zone",
        "required": ["METRIC", "LOG", "TRACE"],
        "tools": ["metrics.query@v1", "logs.query@v1", "traces.query@v1"],
        "chain": [
            ("search-api-memory", "search-api"),
            ("search-api", "checkout-gateway"),
        ],
        "affected": ["search-api", "checkout-gateway"],
        "error": "garbage collection paused request handling while heap pressure rose",
    },
    {
        "id": "elasticsearch-query-latency",
        "title": "Elasticsearch query latency with distractors",
        "service": "catalog-search-api",
        "fault": "query_latency",
        "resource": "elasticsearch-hot-tier",
        "root_type": "dependency_latency",
        "entry": "checkout-gateway",
        "topology": "fan-out",
        "required": ["METRIC", "LOG", "TRACE", "TOPOLOGY"],
        "tools": [
            "metrics.query@v1",
            "logs.query@v1",
            "traces.query@v1",
            "topology.query@v1",
        ],
        "chain": [
            ("elasticsearch-hot-tier", "catalog-search-api"),
            ("catalog-search-api", "checkout-gateway"),
        ],
        "affected": ["catalog-search-api", "checkout-gateway"],
        "error": (
            "search shards exceeded the latency budget while unrelated warnings fired"
        ),
        "composite": True,
    },
    {
        "id": "cross-zone-network-alert-no-impact",
        "title": "Cross-zone network alert without customer impact",
        "service": "shipping-quote-api",
        "fault": "network_alert_no_impact",
        "resource": "cross-zone-link",
        "root_type": None,
        "entry": "checkout-gateway",
        "topology": "cross-zone",
        "required": ["METRIC", "LOG", "TRACE", "TOPOLOGY"],
        "tools": [
            "metrics.query@v1",
            "logs.query@v1",
            "traces.query@v1",
            "topology.query@v1",
        ],
        "chain": [],
        "affected": ["shipping-quote-api"],
        "error": (
            "a low-priority network alert fired while requests remained successful"
        ),
        "no_actionable": True,
    },
)

HOLDOUTS_V2 = (
    {
        "id": "postgresql-deadlock-chain",
        "title": "PostgreSQL deadlock chain",
        "service": "basket-worker",
        "fault": "lock_contention",
        "resource": "orders-db-primary",
        "root_type": "resource_exhaustion",
        "entry": "purchase-gateway",
        "topology": "single-zone",
        "required": ["METRIC", "LOG", "TRACE"],
        "tools": ["metrics.query@v1", "logs.query@v1", "traces.query@v1"],
        "chain": [
            ("orders-db-primary", "basket-worker"),
            ("basket-worker", "purchase-gateway"),
        ],
        "affected": ["basket-worker", "purchase-gateway"],
        "error": "transactions stopped progressing behind a deadlock victim",
    },
    {
        "id": "cassandra-session-deadline",
        "title": "Cassandra session deadline",
        "service": "account-profile-worker",
        "fault": "connection_timeout",
        "resource": "cassandra-ring-a",
        "root_type": "dependency_timeout",
        "entry": "identity-gateway",
        "topology": "cross-zone",
        "required": ["LOG", "TRACE"],
        "tools": ["logs.query@v1", "traces.query@v1"],
        "chain": [
            ("cassandra-ring-a", "account-profile-worker"),
            ("account-profile-worker", "identity-gateway"),
        ],
        "affected": ["account-profile-worker", "identity-gateway"],
        "error": "the storage session deadline expired before a node replied",
    },
    {
        "id": "nats-delivery-lag",
        "title": "NATS delivery lag",
        "service": "email-dispatch-worker",
        "fault": "consumer_backlog",
        "resource": "nats-notifications",
        "root_type": "resource_exhaustion",
        "entry": "message-gateway",
        "topology": "cross-zone",
        "required": ["METRIC", "LOG"],
        "tools": ["metrics.query@v1", "logs.query@v1"],
        "chain": [
            ("nats-notifications", "email-dispatch-worker"),
            ("email-dispatch-worker", "message-gateway"),
        ],
        "affected": ["email-dispatch-worker", "message-gateway"],
        "error": "delivery acknowledgements stalled while pending messages grew",
    },
    {
        "id": "service-discovery-lookup-deadline",
        "title": "Service discovery lookup deadline",
        "service": "promotion-engine-api",
        "fault": "dns_failure",
        "resource": "discovery.internal",
        "root_type": "dependency_timeout",
        "entry": "purchase-gateway",
        "topology": "cross-zone",
        "required": ["LOG", "TRACE"],
        "tools": ["logs.query@v1", "traces.query@v1"],
        "chain": [
            ("discovery.internal", "promotion-engine-api"),
            ("promotion-engine-api", "purchase-gateway"),
        ],
        "affected": ["promotion-engine-api", "purchase-gateway"],
        "error": "the discovery resolver exhausted its bounded lookup budget",
    },
    {
        "id": "sidecar-retry-amplification",
        "title": "Sidecar retry amplification",
        "service": "personalization-worker",
        "fault": "retry_storm",
        "resource": "service-mesh-gateway",
        "root_type": "resource_exhaustion",
        "entry": "purchase-gateway",
        "topology": "mesh-egress",
        "required": ["METRIC", "LOG", "TOPOLOGY"],
        "tools": ["metrics.query@v1", "logs.query@v1", "topology.query@v1"],
        "chain": [
            ("service-mesh-gateway", "personalization-worker"),
            ("personalization-worker", "purchase-gateway"),
        ],
        "affected": ["personalization-worker", "purchase-gateway"],
        "error": "sidecar retries amplified one upstream failure into saturation",
    },
    {
        "id": "blob-store-rate-limit",
        "title": "Blob store rate limit",
        "service": "receipt-generator-api",
        "fault": "storage_throttling",
        "resource": "blob-receipts-container",
        "root_type": "resource_exhaustion",
        "entry": "order-gateway",
        "topology": "cross-zone",
        "required": ["METRIC", "LOG", "TRACE"],
        "tools": ["metrics.query@v1", "logs.query@v1", "traces.query@v1"],
        "chain": [
            ("blob-receipts-container", "receipt-generator-api"),
            ("receipt-generator-api", "order-gateway"),
        ],
        "affected": ["receipt-generator-api", "order-gateway"],
        "error": "the blob backend rejected writes after its quota was exhausted",
    },
    {
        "id": "tax-provider-response-deadline",
        "title": "Tax provider response deadline",
        "service": "tax-calculator-worker",
        "fault": "vendor_timeout",
        "resource": "tax-vendor-edge",
        "root_type": "dependency_timeout",
        "entry": "order-gateway",
        "topology": "single-zone",
        "required": ["LOG", "TRACE"],
        "tools": ["logs.query@v1", "traces.query@v1"],
        "chain": [
            ("tax-vendor-edge", "tax-calculator-worker"),
            ("tax-calculator-worker", "order-gateway"),
        ],
        "affected": ["tax-calculator-worker", "order-gateway"],
        "error": "the external tax response missed the operation deadline",
    },
    {
        "id": "heap-thrashing-pause",
        "title": "Heap thrashing pause",
        "service": "product-indexer-worker",
        "fault": "gc_pause",
        "resource": "indexer-heap-region",
        "root_type": "resource_exhaustion",
        "entry": "catalog-gateway",
        "topology": "single-zone",
        "required": ["METRIC", "LOG", "TRACE"],
        "tools": ["metrics.query@v1", "logs.query@v1", "traces.query@v1"],
        "chain": [
            ("indexer-heap-region", "product-indexer-worker"),
            ("product-indexer-worker", "catalog-gateway"),
        ],
        "affected": ["product-indexer-worker", "catalog-gateway"],
        "error": "repeated full collections paused indexing under heap pressure",
    },
    {
        "id": "opensearch-shard-latency",
        "title": "OpenSearch shard latency",
        "service": "product-discovery-worker",
        "fault": "query_latency",
        "resource": "opensearch-warm-tier",
        "root_type": "dependency_latency",
        "entry": "catalog-gateway",
        "topology": "fan-out",
        "required": ["METRIC", "LOG", "TRACE", "TOPOLOGY"],
        "tools": [
            "metrics.query@v1",
            "logs.query@v1",
            "traces.query@v1",
            "topology.query@v1",
        ],
        "chain": [
            ("opensearch-warm-tier", "product-discovery-worker"),
            ("product-discovery-worker", "catalog-gateway"),
        ],
        "affected": ["product-discovery-worker", "catalog-gateway"],
        "error": "warm-tier shards exceeded the search latency objective",
        "composite": True,
    },
    {
        "id": "inter-region-link-alert-no-impact",
        "title": "Inter-region link alert without impact",
        "service": "fulfillment-router-worker",
        "fault": "network_alert_no_impact",
        "resource": "inter-region-link",
        "root_type": None,
        "entry": "order-gateway",
        "topology": "cross-zone",
        "required": ["METRIC", "LOG", "TRACE", "TOPOLOGY"],
        "tools": [
            "metrics.query@v1",
            "logs.query@v1",
            "traces.query@v1",
            "topology.query@v1",
        ],
        "chain": [],
        "affected": [],
        "error": "a link warning fired while customer requests remained successful",
        "no_actionable": True,
    },
)


def _public(item: dict[str, object]) -> dict[str, object]:
    service = str(item["service"])
    scenario_id = str(item["id"])
    return {
        "schema_version": "1.0",
        "scenario_id": scenario_id,
        "title": str(item["title"]),
        "description": "Hidden holdout scenario for black-box generalization.",
        "service_name": service,
        "fault_type": str(item["fault"]),
        "injection": {
            "method": "POST",
            "path": f"/holdout/faults/{scenario_id}",
            "json_body": {"duration_seconds": 60, "executor": "blackbox"},
            "expected_status": 200,
        },
        "trigger": {
            "method": "POST",
            "path": f"/holdout/{item['entry']}/request",
            "expected_status": 200 if item.get("no_actionable", False) else 503,
        },
        "cleanup": {
            "method": "POST",
            "path": "/holdout/faults/reset",
            "expected_status": 200,
        },
        "alert_mapping": {
            "source": "alertmanager",
            "service_name": service,
            "alert_name": "Holdout"
            + "".join(part.capitalize() for part in scenario_id.split("-")),
            "summary": f"{service} operational anomaly requires investigation",
            "source_severity": "P1",
            "platform_severity": "CRITICAL",
        },
        "environment": {
            "benchmark_split": "hidden_holdout",
            "topology_variant": item["topology"],
            "resource_system": item["resource"],
            "service_name_randomization": True,
            "composite_fault": bool(item.get("composite", False)),
            "no_actionable_root_cause": bool(item.get("no_actionable", False)),
        },
    }


def _private(item: dict[str, object]) -> dict[str, object]:
    chain = [
        {"from_node": source, "to_node": target}
        for source, target in item["chain"]  # type: ignore[union-attr]
    ]
    service = str(item["service"])
    no_actionable = bool(item.get("no_actionable", False))
    return {
        "schema_version": "1.0",
        "scenario_id": str(item["id"]),
        "ground_truth": {
            "root_cause": (
                None
                if no_actionable
                else {
                    "service_name": service,
                    "root_cause_type": str(item["root_type"]),
                    "root_cause_resource": str(item["resource"]),
                }
            ),
            "expected_conclusion_status": (
                "NO_ACTIONABLE_ROOT_CAUSE" if no_actionable else "CANDIDATE"
            ),
            "required_evidence": [
                f"{item['id']}-evidence-{index}"
                for index, _ in enumerate(
                    item["required"], start=1
                )  # type: ignore[arg-type]
            ],
            "required_evidence_types": item["required"],
            "optional_evidence_types": ["CHANGE", "KNOWLEDGE", "RUNBOOK"],
            "causal_chain": chain,
            "affected_services": [] if no_actionable else item["affected"],
            "forbidden_claims": [
                "The holdout answer is known from the scenario manifest.",
                "Data corruption is confirmed without direct evidence.",
            ],
            "expected_tool_types": item["tools"],
            "forbidden_tool_types": ["shell", "remediation.execute@v1"],
        },
    }


def generate(
    public_directory: Path,
    private_directory: Path,
    *,
    version: str = "v1",
) -> tuple[str, ...]:
    catalog = {"v1": HOLDOUTS, "v2": HOLDOUTS_V2}.get(version)
    if catalog is None:
        raise ValueError("version must be v1 or v2")
    public_directory.mkdir(parents=True, exist_ok=True)
    private_directory.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for item in catalog:
        name = f"{item['id']}.json"
        public_path = public_directory / name
        private_path = private_directory / name
        if public_path.exists() or private_path.exists():
            raise ValueError(f"refusing to overwrite holdout {name}")
        public_path.write_text(
            json.dumps(_public(item), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        private_path.write_text(
            json.dumps(_private(item), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        created.extend((str(public_path), str(private_path)))
    return tuple(created)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", required=True, type=Path)
    parser.add_argument("--private", required=True, type=Path)
    parser.add_argument("--version", choices=("v1", "v2"), default="v1")
    args = parser.parse_args(argv)
    for path in generate(args.public, args.private, version=args.version):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
