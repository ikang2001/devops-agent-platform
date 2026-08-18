from __future__ import annotations

import json
from pathlib import Path

import yaml

from ops.evaluation.build_reference_inputs import build_reference_suite

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = PROJECT_ROOT / "ops" / "reference-staging"
SCENARIO_ROOT = PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios"


def test_reference_staging_combines_all_declared_dependencies() -> None:
    compose = yaml.safe_load(
        (REFERENCE_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    assert {
        "postgres",
        "redpanda",
        "oidc",
        "provider-mock",
        "mcp-server",
        "migrate",
        "agent",
        "prometheus",
        "loki",
        "tempo",
    }.issubset(services)
    environment = services["agent"]["environment"]
    assert environment["DEVOPS_AGENT_ADMIN_OIDC_ENABLED"] == "true"
    assert environment["DEVOPS_AGENT_ADMIN_DEMO_ENABLED"] == "false"
    assert environment["DEVOPS_AGENT_ADMIN_OIDC_CA_BUNDLE_PATH"] == "/certs/ca.pem"
    assert environment["DEVOPS_AGENT_TICKETING_HTTP_JSON_ENABLED"] == "true"
    assert "synthetic" in (REFERENCE_ROOT / "README.md").read_text(encoding="utf-8")


def test_reference_runtime_builder_covers_twelve_cases_without_ground_truth() -> None:
    suite = build_reference_suite(SCENARIO_ROOT)

    assert suite["synthetic"] is True
    assert len(suite["cases"]) == 12
    serialized = json.dumps(suite, ensure_ascii=False)
    assert "ground_truth" not in serialized
    assert "forbidden_claims" not in serialized
    assert "expected_tool_types" not in serialized
