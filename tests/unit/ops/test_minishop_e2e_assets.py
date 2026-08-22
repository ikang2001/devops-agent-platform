import importlib.util
import json
import sys
from datetime import UTC, datetime
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
    assert services["alertmanager"]["image"] == ("prom/alertmanager:v0.32.0")
    agent_environment = compose["x-agent-environment"]
    assert agent_environment["DEVOPS_AGENT_OUTBOX_WORKER_ENABLED"] == "true"
    assert agent_environment["DEVOPS_AGENT_RCA_CONSUMER_ENABLED"] == "true"
    assert agent_environment["DEVOPS_AGENT_ADMIN_DEMO_ENABLED"] == "true"
    assert (
        agent_environment["DEVOPS_AGENT_METRICS_QUERY_AVAILABILITY_METRIC"]
        == "minishop_service_up"
    )
    runner_environment = services["e2e-runner"]["environment"]
    assert runner_environment["E2E_WEBHOOK_SECRET"]
    minishop_environment = services["minishop"]["environment"]
    assert minishop_environment["MINISHOP_AGENT_URL"].endswith("/api/v1/alerts")
    assert minishop_environment["MINISHOP_OTLP_TRACES_ENDPOINT"].endswith("/v1/traces")
    assert "MINISHOP_E2E_POSTGRES_PORT" in str(services["postgres"]["ports"])
    assert "MINISHOP_E2E_MINISHOP_PORT" in str(services["minishop"]["ports"])


def test_observability_configs_preserve_tenant_and_service_contracts() -> None:
    prometheus = _load_yaml("prometheus.yml")
    alerts = _load_yaml("alerts.yml")
    promtail = _load_yaml("promtail.yml")

    assert prometheus["global"]["scrape_interval"] == "2s"
    alert_names = {rule["alert"] for rule in alerts["groups"][0]["rules"]}
    assert alert_names == {
        "MiniShopCheckoutHighLatency",
        "MiniShopPaymentDeploymentRegression",
        "MiniShopInventoryDbTimeout",
        "MiniShopPayment5xxSpike",
        "MiniShopPaymentConfigRegression",
        "MiniShopInventoryRedisLatency",
        "MiniShopCheckoutPoolExhaustion",
        "MiniShopPaymentProviderTimeout",
        "MiniShopCheckoutCascade",
        "MiniShopKnownPaymentError",
        "MiniShopMisleadingPaymentHistory",
        "MiniShopFalsePositiveCheckout",
        "HoldoutCrossZoneNetworkAlertNoImpact",
        "HoldoutDnsResolutionFailure",
        "HoldoutElasticsearchQueryLatency",
        "HoldoutMemoryLeakGcPause",
        "HoldoutMongodbConnectionTimeout",
        "HoldoutMysqlLockContention",
        "HoldoutObjectStorageThrottling",
        "HoldoutRabbitmqConsumerBacklog",
        "HoldoutServiceMeshRetryStorm",
        "HoldoutThirdPartySmsTimeout",
        "HoldoutPostgresqlDeadlockChain",
        "HoldoutCassandraSessionDeadline",
        "HoldoutNatsDeliveryLag",
        "HoldoutServiceDiscoveryLookupDeadline",
        "HoldoutSidecarRetryAmplification",
        "HoldoutBlobStoreRateLimit",
        "HoldoutTaxProviderResponseDeadline",
        "HoldoutHeapThrashingPause",
        "HoldoutOpensearchShardLatency",
        "HoldoutInterRegionLinkAlertNoImpact",
    }
    expressions = promtail["scrape_configs"][0]["pipeline_stages"][0]["json"][
        "expressions"
    ]
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


def test_blackbox_executor_maps_domain_evidence_aliases() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    path = E2E_ROOT / "blackbox_executor.py"
    sys.path.insert(0, str(E2E_ROOT))
    spec = spec_from_file_location("minishop_blackbox_executor", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(E2E_ROOT))

    assert (
        module.MiniShopBlackBoxExecutor._map_evidence_type("DEPLOYMENT").value
        == "CHANGE"
    )
    assert (
        module.MiniShopBlackBoxExecutor._map_evidence_type("INCIDENT_HISTORY").value
        == "KNOWLEDGE"
    )


def test_blackbox_executor_settles_incident_between_scenarios() -> None:
    source = (E2E_ROOT / "blackbox_executor.py").read_text(encoding="utf-8")

    assert "self._settle_matching_incidents(" in source
    settlement = source.split("def _settle_matching_incidents", 1)[1].split(
        "def _start_rca", 1
    )[0]
    assert 'item.get("service_name") == scenario.service_name' in settlement
    assert 'item.get("title")' not in settlement
    assert 'f"{base}/resolution"' in source
    assert 'f"{base}/closure"' in source


def test_blackbox_executor_accepts_correlated_same_service_incident() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    path = E2E_ROOT / "blackbox_executor.py"
    sys.path.insert(0, str(E2E_ROOT))
    spec = spec_from_file_location("minishop_blackbox_incident_executor", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(E2E_ROOT))

    scenario = type("Scenario", (), {"service_name": "checkout-service"})()
    started_at = datetime(2026, 8, 21, 17, 36, tzinfo=UTC)
    correlated = {
        "incident_id": "inc-correlated",
        "service_name": "checkout-service",
        "title": "checkout-service p95 latency is higher than 1s",
        "status": "OPEN",
        "created_at": "2026-08-21T17:36:54+00:00",
    }

    selected = module.MiniShopBlackBoxExecutor._select_incident(
        [correlated],
        scenario=scenario,
        expected_title="checkout downstream cascade",
        started_at=started_at,
    )

    assert selected == correlated


def test_blackbox_executor_maps_platform_candidate_snapshot() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    path = E2E_ROOT / "blackbox_executor.py"
    sys.path.insert(0, str(E2E_ROOT))
    spec = spec_from_file_location("minishop_blackbox_candidate_executor", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(E2E_ROOT))

    candidate = module.MiniShopBlackBoxExecutor._candidate(
        {
            "candidate_id": "cand-checkout",
            "service": "checkout-service",
            "root_type": "application_error",
            "resource": None,
            "score": 0.9,
            "supporting_evidence_ids": ["ev-log"],
            "contradicting_evidence_ids": [],
            "source_evidence_types": ["LOG"],
            "missing_evidence": [],
        }
    )

    assert candidate.root_cause.service == "checkout-service"
    assert candidate.root_cause.type == "application_error"
    assert candidate.missing_evidence == ()


def test_blackbox_executor_resolves_holdout_entry_service() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    path = E2E_ROOT / "blackbox_executor.py"
    sys.path.insert(0, str(E2E_ROOT))
    spec = spec_from_file_location("minishop_blackbox_entry_executor", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(E2E_ROOT))

    scenario = type(
        "Scenario",
        (),
        {"trigger": {"path": "/holdout/checkout-gateway/request"}},
    )()
    assert module.MiniShopBlackBoxExecutor._entry_service(scenario) == (
        "checkout-gateway"
    )


def test_blackbox_executor_does_not_reuse_permission_idempotency_key() -> None:
    source = (E2E_ROOT / "blackbox_executor.py").read_text(encoding="utf-8")

    assert 'f"blackbox-permissions-{uuid.uuid4().hex}"' in source


def test_blackbox_executor_waits_for_alert_to_be_inactive() -> None:
    source = (E2E_ROOT / "blackbox_executor.py").read_text(encoding="utf-8")

    assert 'rules[0].get("state") != "inactive"' in source
    assert "now - inactive_since >= 4" in source
    assert "alert did not clear" in source


def test_blackbox_executor_waits_for_complete_terminal_projection() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    path = E2E_ROOT / "blackbox_executor.py"
    sys.path.insert(0, str(E2E_ROOT))
    spec = spec_from_file_location("minishop_blackbox_projection_executor", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(E2E_ROOT))

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self._data = data

        def json(self) -> dict[str, object]:
            return {"success": True, "data": self._data}

    class Http:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, *_args, **_kwargs) -> Response:
            self.calls += 1
            if self.calls == 1:
                return Response(
                    {"status": "SUCCEEDED", "evidence": [], "report": None}
                )
            return Response(
                {
                    "status": "SUCCEEDED",
                    "evidence": [{"evidence_id": "ev-log"}],
                    "report": {"conclusion_status": "UNDETERMINED"},
                }
            )

        def close(self) -> None:
            pass

    def wait_for_projection(_name, callback, **_kwargs):
        assert callback() is None
        return callback()

    executor = module.MiniShopBlackBoxExecutor(
        module.E2EConfig(agent_url="http://agent")
    )
    executor.http.close()
    executor.http = Http()
    module.wait_for_value = wait_for_projection

    result = executor._wait_for_workflow("workflow-1")

    assert executor.http.calls == 2
    assert result["evidence"] == [{"evidence_id": "ev-log"}]


def test_v07_host_scripts_support_the_hidden_holdout_catalog() -> None:
    for name in ("run-v07-blackbox.ps1", "run-v07-blackbox-host.ps1"):
        source = (E2E_ROOT / name).read_text(encoding="utf-8")
        assert '[ValidateSet("Known", "Holdout", "HoldoutV2")]' in source
        assert '"scenarios\\holdout"' in source


def test_holdout_alert_summaries_match_public_catalog() -> None:
    alerts = _load_yaml("alerts.yml")
    rules = {rule["alert"]: rule for rule in alerts["groups"][0]["rules"]}
    public_root = (
        PROJECT_ROOT
        / "MiniShop 电商下单故障演练靶场"
        / "scenarios"
        / "holdout"
        / "public"
    )
    for path in public_root.glob("*.json"):
        public = json.loads(path.read_text(encoding="utf-8"))
        mapping = public["alert_mapping"]
        rule = rules[mapping["alert_name"]]
        assert rule["annotations"]["summary"] == mapping["summary"]
        assert rule["labels"]["service_name"] == public["service_name"]


def test_e2e_runner_records_signed_change_and_closes_each_incident() -> None:
    source = (E2E_ROOT / "run_e2e.py").read_text(encoding="utf-8")

    assert 'f"{self.config.agent_url}/api/v1/change-events"' in source
    assert "X-DevOps-Agent-Signature" in source
    assert '"changes:read"' in source
    assert 'self._settle_incident(incident["incident_id"])' in source
    assert "SCENARIO_EXECUTION_ORDER" in source
    assert 'incident["title"] == scenario.alert_mapping.summary' in source


def test_model_stub_requires_corroboration_for_deployment_regression() -> None:
    stub = _load_llm_stub()
    change = {
        "evidence_id": "ev-change",
        "evidence_type": "CHANGE",
        "source": "change_event_store",
        "summary": (
            "Collected deployment change for payment-service: "
            "payment-service:v1->v2 status=SUCCEEDED; "
            "minutes_from_incident=-0.05"
        ),
    }
    only_change = {
        "messages": [
            {
                "role": "user",
                "content": json.dumps({"evidence": [change]}),
            }
        ]
    }

    assert stub.build_report(only_change)["conclusion_status"] == "UNDETERMINED"

    corroborated = {
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "evidence": [
                            change,
                            {
                                "evidence_id": "ev-metric",
                                "evidence_type": "METRIC",
                                "source": "prometheus",
                                "summary": (
                                    "payment-service v2 deployment_regression error "
                                    "rate increased"
                                ),
                            },
                            {
                                "evidence_id": "ev-log",
                                "evidence_type": "LOG",
                                "source": "loki",
                                "summary": (
                                    "payment-service v2 returned "
                                    "PAYMENT_DEPLOYMENT_REGRESSION"
                                ),
                            },
                        ]
                    }
                ),
            }
        ]
    }
    report = stub.build_report(corroborated)

    assert report["conclusion_status"] == "CANDIDATE"
    assert "deployment_regression" in report["summary"]


def test_model_stub_does_not_treat_unrelated_change_as_deployment_regression() -> None:
    stub = _load_llm_stub()
    request = {
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "evidence": [
                            {
                                "evidence_id": "ev-change",
                                "evidence_type": "CHANGE",
                                "source": "change_event_store",
                                "summary": (
                                    "Collected CONFIG change for payment-service"
                                ),
                            },
                            {
                                "evidence_id": "ev-metric",
                                "evidence_type": "METRIC",
                                "source": "prometheus",
                                "summary": "payment-service errors increased",
                            },
                            {
                                "evidence_id": "ev-log",
                                "evidence_type": "LOG",
                                "source": "loki",
                                "summary": "payment-service payment_error",
                            },
                        ]
                    }
                ),
            }
        ]
    }

    report = stub.build_report(request)

    assert "deployment_regression" not in report["summary"]
    assert "payment_error" in report["summary"]


def test_model_stub_does_not_reuse_prior_deployment_for_payment_error() -> None:
    stub = _load_llm_stub()
    request = {
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "evidence": [
                            {
                                "evidence_id": "ev-prior-deployment",
                                "evidence_type": "CHANGE",
                                "source": "change_event_store",
                                "summary": (
                                    "latest=DEPLOYMENT payment-service:v1->v2 "
                                    "status=SUCCEEDED; minutes_from_incident=-5.0"
                                ),
                            },
                            {
                                "evidence_id": "ev-payment-metric",
                                "evidence_type": "METRIC",
                                "source": "prometheus",
                                "summary": "payment-service payment_error increased",
                            },
                            {
                                "evidence_id": "ev-payment-log",
                                "evidence_type": "LOG",
                                "source": "loki",
                                "summary": "PAYMENT_GATEWAY_ERROR during checkout",
                            },
                        ]
                    }
                ),
            }
        ]
    }

    report = stub.build_report(request)

    assert report["conclusion_status"] == "CANDIDATE"
    assert "deployment_regression" not in report["summary"]
    assert "payment_error" in report["summary"]
