from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.scenario_manifest import (
    SCENARIO_DIRECTORY,
    ScenarioCatalog,
    ScenarioManifest,
    load_scenario,
    load_scenario_catalog,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _payload(scenario_id: str) -> dict:
    scenario = load_scenario(SCENARIO_DIRECTORY / f"{scenario_id}.json")
    return scenario.model_dump(mode="json")


def test_default_catalog_contains_complete_ground_truth_for_all_faults():
    catalog = load_scenario_catalog()

    assert {scenario.scenario_id for scenario in catalog.scenarios} == {
        "checkout-latency",
        "deployment-regression",
        "inventory-db-timeout",
        "payment-error",
    }
    for scenario in catalog.scenarios:
        assert scenario.injection.path.startswith("/faults/")
        assert scenario.trigger.path == "/checkout"
        assert scenario.cleanup.path == "/faults/reset"
        assert scenario.alert_mapping.platform_severity == "CRITICAL"
        assert scenario.expected_signals
        assert scenario.ground_truth.required_evidence
        assert scenario.ground_truth.required_evidence_types
        assert scenario.ground_truth.causal_chain
        assert scenario.ground_truth.affected_services
        assert scenario.ground_truth.forbidden_claims
        assert scenario.ground_truth.expected_tool_types
        assert scenario.ground_truth.forbidden_tool_types
        assert (PROJECT_ROOT / scenario.ground_truth.root_cause.runbook_path).is_file()


def test_every_ground_truth_requires_its_tempo_signal():
    catalog = load_scenario_catalog()

    for scenario in catalog.scenarios:
        tempo_signals = [signal for signal in scenario.expected_signals if signal.source == "tempo"]
        assert len(tempo_signals) == 1
        assert tempo_signals[0].evidence_id in scenario.ground_truth.required_evidence
        assert "resource.service.name" in tempo_signals[0].locator


@pytest.mark.parametrize(
    "scenario_id",
    [
        "checkout-latency",
        "deployment-regression",
        "inventory-db-timeout",
        "payment-error",
    ],
)
def test_manifest_actions_execute_against_minishop(scenario_id: str):
    scenario = load_scenario(SCENARIO_DIRECTORY / f"{scenario_id}.json")
    client = TestClient(app)
    client.post("/faults/reset")

    try:
        injection_response = client.request(
            scenario.injection.method,
            scenario.injection.path,
            json=scenario.injection.json_body,
        )
        assert injection_response.status_code == scenario.injection.expected_status

        trigger_response = client.request(
            scenario.trigger.method,
            scenario.trigger.path,
            json=scenario.trigger.json_body,
        )
        assert trigger_response.status_code == scenario.trigger.expected_status
    finally:
        cleanup_response = client.request(
            scenario.cleanup.method,
            scenario.cleanup.path,
            json=scenario.cleanup.json_body,
        )
        assert cleanup_response.status_code == scenario.cleanup.expected_status

    assert client.get("/faults").json()["faults"] == []


def test_catalog_rejects_duplicate_scenario_ids():
    scenario = load_scenario(SCENARIO_DIRECTORY / "payment-error.json")

    with pytest.raises(ValidationError, match="scenario_id values must be unique"):
        ScenarioCatalog(scenarios=[scenario, scenario])


def test_deployment_regression_requires_change_and_corroborating_evidence():
    scenario = load_scenario(SCENARIO_DIRECTORY / "deployment-regression.json")

    assert scenario.ground_truth.root_cause.root_cause_type == ("deployment_regression")
    assert scenario.ground_truth.root_cause.root_cause_resource == ("payment-service:v2")
    assert set(scenario.ground_truth.required_evidence_types) == {
        "CHANGE",
        "LOG",
        "METRIC",
        "TRACE",
    }
    assert "changes.query@v1" in scenario.ground_truth.expected_tool_types


def test_directory_loader_applies_catalog_uniqueness_validation(tmp_path: Path):
    manifest = (SCENARIO_DIRECTORY / "payment-error.json").read_text(encoding="utf-8")
    (tmp_path / "first.json").write_text(manifest, encoding="utf-8")
    (tmp_path / "second.json").write_text(manifest, encoding="utf-8")

    with pytest.raises(ValidationError, match="scenario_id values must be unique"):
        load_scenario_catalog(tmp_path)


def test_manifest_rejects_unsupported_service_fault_pair():
    payload = _payload("payment-error")
    payload["fault_type"] = "db_timeout"

    with pytest.raises(ValidationError, match="unsupported fault_type"):
        ScenarioManifest.model_validate(payload)


def test_manifest_rejects_fault_identity_with_the_wrong_injection_endpoint():
    payload = _payload("payment-error")
    payload["injection"]["path"] = "/faults/checkout-latency"

    with pytest.raises(ValidationError, match="injection path must match"):
        ScenarioManifest.model_validate(payload)


def test_manifest_rejects_incorrect_alert_severity_mapping():
    payload = _payload("payment-error")
    payload["alert_mapping"]["platform_severity"] = "WARNING"

    with pytest.raises(ValidationError, match="P1 must map to platform severity CRITICAL"):
        ScenarioManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("section", "replacement", "message"),
    [
        (
            "injection",
            {"service_name": "checkout-service", "fault_type": "latency"},
            "injection identity must match scenario identity",
        ),
        (
            "alert_mapping",
            {"service_name": "checkout-service"},
            "alert_mapping service_name must match scenario service_name",
        ),
        (
            "root_cause",
            {"service_name": "checkout-service", "fault_type": "latency"},
            "root cause identity must match scenario identity",
        ),
    ],
)
def test_manifest_rejects_inconsistent_fault_identity(section, replacement, message):
    payload = _payload("inventory-db-timeout")
    target = payload["ground_truth"]["root_cause"] if section == "root_cause" else payload[section]
    target.update(replacement)

    with pytest.raises(ValidationError, match=message):
        ScenarioManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("section", "unsafe_path"),
    [
        ("injection", "https://example.invalid/faults/payment-error"),
        ("trigger", "/checkout/../admin"),
        ("cleanup", "//example.invalid/faults/reset"),
    ],
)
def test_manifest_rejects_unsafe_http_paths(section: str, unsafe_path: str):
    payload = _payload("payment-error")
    payload[section]["path"] = unsafe_path

    with pytest.raises(ValidationError, match="API path"):
        ScenarioManifest.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_path",
    ["../runbooks/payment-5xx-spike.md", "runbooks/../secrets.md", "C:\\secrets.md"],
)
def test_manifest_rejects_unsafe_runbook_paths(unsafe_path: str):
    payload = _payload("payment-error")
    payload["ground_truth"]["root_cause"]["runbook_path"] = unsafe_path

    with pytest.raises(ValidationError, match="project path|runbook path"):
        ScenarioManifest.model_validate(payload)


def test_manifest_rejects_unknown_required_evidence_reference():
    payload = _payload("checkout-latency")
    payload["ground_truth"]["required_evidence"].append("ev-does-not-exist")

    with pytest.raises(ValidationError, match="required_evidence references unknown signal IDs"):
        ScenarioManifest.model_validate(payload)


def test_manifest_rejects_signal_source_evidence_type_mismatch():
    payload = _payload("payment-error")
    payload["expected_signals"][0]["evidence_type"] = "LOG"

    with pytest.raises(ValidationError, match="prometheus signal must use evidence_type METRIC"):
        ScenarioManifest.model_validate(payload)


def test_manifest_rejects_ground_truth_evidence_type_drift():
    payload = _payload("inventory-db-timeout")
    payload["ground_truth"]["required_evidence_types"] = ["METRIC", "TRACE"]

    with pytest.raises(ValidationError, match="required_evidence_types must match"):
        ScenarioManifest.model_validate(payload)


def test_manifest_rejects_forbidden_tool_as_expected():
    payload = _payload("checkout-latency")
    payload["ground_truth"]["expected_tool_types"].append("shell")

    with pytest.raises(ValidationError, match="expected_tool_types and forbidden_tool_types"):
        ScenarioManifest.model_validate(payload)
