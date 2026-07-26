import importlib.util
import json
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
E2E_ROOT = PROJECT_ROOT / "ops" / "minishop-e2e"


def _load_yaml(name: str) -> dict:
    document = yaml.safe_load((E2E_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _load_llm_stub():
    path = E2E_ROOT / "llm_stub.py"
    spec = importlib.util.spec_from_file_location("minishop_e2e_llm_stub", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_contains_the_real_rca_dependency_chain() -> None:
    compose = _load_yaml("docker-compose.yml")
    services = compose["services"]

    assert {
        "agent",
        "alertmanager",
        "e2e-runner",
        "llm-stub",
        "loki",
        "migrate",
        "minishop",
        "postgres",
        "prometheus",
        "promtail",
        "redpanda",
        "tempo",
    }.issubset(services)
    assert services["alertmanager"]["image"] == (
        "prom/alertmanager:v0.32.0"
    )
    agent_environment = compose["x-agent-environment"]
    assert agent_environment["DEVOPS_AGENT_OUTBOX_WORKER_ENABLED"] == "true"
    assert agent_environment["DEVOPS_AGENT_RCA_CONSUMER_ENABLED"] == "true"
    assert agent_environment["DEVOPS_AGENT_ADMIN_DEMO_ENABLED"] == "true"
    assert (
        agent_environment["DEVOPS_AGENT_METRICS_QUERY_AVAILABILITY_METRIC"]
        == "minishop_service_up"
    )
    minishop_environment = services["minishop"]["environment"]
    assert minishop_environment["MINISHOP_AGENT_URL"].endswith("/api/v1/alerts")
    assert minishop_environment["MINISHOP_OTLP_TRACES_ENDPOINT"].endswith(
        "/v1/traces"
    )


def test_observability_configs_preserve_tenant_and_service_contracts() -> None:
    prometheus = _load_yaml("prometheus.yml")
    alerts = _load_yaml("alerts.yml")
    promtail = _load_yaml("promtail.yml")

    assert prometheus["global"]["scrape_interval"] == "2s"
    alert_names = {
        rule["alert"] for rule in alerts["groups"][0]["rules"]
    }
    assert alert_names == {
        "MiniShopCheckoutHighLatency",
        "MiniShopInventoryDbTimeout",
        "MiniShopPayment5xxSpike",
    }
    expressions = promtail["scrape_configs"][0]["pipeline_stages"][0][
        "json"
    ]["expressions"]
    assert expressions["tenant_id"] == "tenant_id"
    assert expressions["service"] == "service_name"
    assert expressions["service_name"] == "service_name"


def test_model_stub_returns_candidate_bound_to_supplied_evidence_ids() -> None:
    stub = _load_llm_stub()
    evidence = [
        {
            "evidence_id": "ev-runbook",
            "source": "runbook_catalog",
            "summary": "Retrieved published runbooks for service payment-service",
        },
        {
            "evidence_id": "ev-logs",
            "source": "loki",
            "summary": "logs.query collected evidence",
        },
    ]
    request = {
        "messages": [
            {"role": "system", "content": "fixed"},
            {"role": "user", "content": json.dumps({"evidence": evidence})},
        ]
    }

    report = stub.build_report(request)

    assert report["conclusion_status"] == "CANDIDATE"
    assert "payment-service" in report["summary"]
    assert "payment_error" in report["summary"]
    assert report["evidence_ids"] == ["ev-runbook", "ev-logs"]
